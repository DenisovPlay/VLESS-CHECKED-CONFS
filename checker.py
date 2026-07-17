"""
checker.py — проверяет VPN конфиги через xray + реальный HTTP GET

Алгоритм для каждого конфига:
1. Парсим URI → xray outbound dict
2. Берём свободный порт
3. Записываем xray JSON-конфиг во временный файл
4. Запускаем xray процесс
5. Ждём 1 сек (xray startup)
6. GET http://cp.cloudflare.com/ через SOCKS5 127.0.0.1:<port>
7. Если 200 → живой
8. Убиваем xray, удаляем tmp файл
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import socket
import sys
import tempfile
import time
from pathlib import Path
from typing import NamedTuple

import httpx

from collector import Config
from xray_manager import build_xray_config, ensure_xray, parse_uri_to_outbound

logger = logging.getLogger(__name__)

# URL для проверки — всегда возвращает 200
CHECK_URL = "http://cp.cloudflare.com/"
# Fallback URL
CHECK_URL_FALLBACK = "http://www.gstatic.com/generate_204"

XRAY_STARTUP_DELAY = 1.2   # секунды ожидания после запуска xray
PROXY_TIMEOUT = 15.0        # таймаут HTTP-запроса через прокси


class CheckResult(NamedTuple):
    config: Config
    alive: bool
    latency_ms: int | None     # None если мёртвый
    error: str | None


def _find_free_port() -> int:
    """Находит свободный TCP-порт на localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _check_one(
    config: Config,
    xray_bin: Path,
    semaphore: asyncio.Semaphore,
) -> CheckResult:
    """Проверяет один конфиг. Всегда возвращает CheckResult (не бросает исключения)."""
    async with semaphore:
        # Парсим URI
        outbound = parse_uri_to_outbound(config.uri)
        if outbound is None:
            return CheckResult(config=config, alive=False, latency_ms=None, error="parse_failed")

        port = _find_free_port()
        xray_cfg = build_xray_config(outbound, socks_port=port)

        # Пишем xray конфиг во временный файл
        tmp_file = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            delete=False,
            encoding="utf-8",
        )
        try:
            json.dump(xray_cfg, tmp_file, ensure_ascii=False)
            tmp_file.flush()
            tmp_file.close()

            # Запускаем xray
            proc = await asyncio.create_subprocess_exec(
                str(xray_bin), "run", "-c", tmp_file.name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )

            try:
                # Ждём старта xray
                await asyncio.sleep(XRAY_STARTUP_DELAY)

                # Проверяем через прокси
                t0 = time.monotonic()
                alive, error = await _http_get_via_socks5(port)
                latency_ms = int((time.monotonic() - t0) * 1000) if alive else None

                return CheckResult(
                    config=config,
                    alive=alive,
                    latency_ms=latency_ms,
                    error=error,
                )
            finally:
                # Убиваем xray процесс
                try:
                    if sys.platform == "win32":
                        proc.kill()
                    else:
                        proc.send_signal(signal.SIGTERM)
                    await asyncio.wait_for(proc.wait(), timeout=3.0)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass

        finally:
            # Удаляем временный файл
            try:
                os.unlink(tmp_file.name)
            except OSError:
                pass


async def _http_get_via_socks5(socks_port: int) -> tuple[bool, str | None]:
    """
    Делает HTTP GET через SOCKS5 прокси.
    Возвращает (alive: bool, error: str | None).
    """
    proxy_url = f"socks5://127.0.0.1:{socks_port}"

    for check_url in (CHECK_URL, CHECK_URL_FALLBACK):
        try:
            async with httpx.AsyncClient(
                proxy=proxy_url,
                timeout=PROXY_TIMEOUT,
                verify=False,
                follow_redirects=True,
            ) as client:
                resp = await client.get(check_url)
                if resp.status_code in (200, 204, 301, 302):
                    return True, None
                return False, f"http_{resp.status_code}"
        except httpx.ProxyError as e:
            return False, f"proxy_error: {type(e).__name__}"
        except httpx.ConnectTimeout:
            return False, "connect_timeout"
        except httpx.ReadTimeout:
            return False, "read_timeout"
        except httpx.ConnectError:
            # Возможно xray ещё не поднялся на этом порту, пробуем fallback
            continue
        except Exception as e:
            return False, f"error: {type(e).__name__}"

    return False, "all_urls_failed"


async def check_all(
    configs: list[Config],
    workers: int = 50,
    progress: bool = True,
) -> list[CheckResult]:
    """
    Проверяет все конфиги параллельно.

    Args:
        configs: список конфигов из collector
        workers: количество параллельных воркеров
        progress: выводить ли прогресс

    Returns:
        Список CheckResult (живые и мёртвые)
    """
    xray_bin = await ensure_xray()
    semaphore = asyncio.Semaphore(workers)

    total = len(configs)
    done = 0
    alive_count = 0
    results: list[CheckResult] = []

    logger.info("Запускаем проверку %d конфигов (%d воркеров)...", total, workers)

    async def _wrapped(cfg: Config) -> CheckResult:
        nonlocal done, alive_count
        result = await _check_one(cfg, xray_bin, semaphore)
        done += 1
        if result.alive:
            alive_count += 1
        if progress:
            status = "✓" if result.alive else "✗"
            latency = f" {result.latency_ms}ms" if result.latency_ms else ""
            print(
                f"\r  [{done}/{total}] живых: {alive_count}{status}{latency}    ",
                end="",
                flush=True,
            )
        return result

    tasks = [_wrapped(cfg) for cfg in configs]
    results = await asyncio.gather(*tasks)

    if progress:
        print()  # новая строка после прогресса

    alive = [r for r in results if r.alive]
    dead = [r for r in results if not r.alive]
    logger.info(
        "Результат: %d живых / %d мёртвых из %d",
        len(alive), len(dead), total,
    )
    return list(results)


if __name__ == "__main__":
    import sys
    from collector import collect
    import logging

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    workers = int(sys.argv[1]) if len(sys.argv) > 1 else 50

    async def _main():
        configs = await collect()
        results = await check_all(configs, workers=workers)
        alive = [r for r in results if r.alive]
        print(f"\nЖивых: {len(alive)}/{len(results)}")

    asyncio.run(_main())
