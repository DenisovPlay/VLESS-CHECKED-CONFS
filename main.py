"""
main.py — оркестратор полного pipeline:
  1. Collect — собрать конфиги из всех источников
  2. Check   — проверить через xray + HTTP GET
  3. Write   — записать output/safe.txt и output/white.txt
  4. Push    — git commit + push

Запуск:
  python main.py                    # полный цикл
  python main.py --collect-only     # только сбор без проверки
  python main.py --workers 30       # кастомное количество воркеров
  python main.py --no-push          # без git push
  python main.py --limit 100        # ограничить кол-во конфигов (для теста)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import subprocess
import sys
import time
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import base64
from checker import CheckResult, check_all
from collector import Config, collect
from country_helper import enrich_config_with_country

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).parent / "output"
SAFE_FILE = OUTPUT_DIR / "safe.txt"
SAFE_MOBILE_FILE = OUTPUT_DIR / "safe_mobile.txt"
WHITE_FILE = OUTPUT_DIR / "white.txt"
WHITE_MOBILE_FILE = OUTPUT_DIR / "white_mobile.txt"
ALL_FILE = OUTPUT_DIR / "all.txt"
BAD_CONFIGS_FILE = Path(__file__).parent / ".bad_configs.txt"


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    # Заглушаем шумные библиотеки
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _get_clean_uri_hash(uri: str) -> str:
    """Возвращает SHA-256 хеш очищенного URI (без ремарки после #)."""
    clean_uri = uri.split("#")[0]
    return hashlib.sha256(clean_uri.encode("utf-8")).hexdigest()


def _load_bad_configs() -> set[str]:
    """Загружает хеши неработающих конфигов, игнорируя те, что старше 7 дней."""
    bad_hashes = set()
    if not BAD_CONFIGS_FILE.exists():
        return bad_hashes

    current_time = int(time.time())
    expired_count = 0
    loaded_count = 0

    try:
        with open(BAD_CONFIGS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or "," not in line:
                    continue
                h, ts_str = line.split(",", 1)
                try:
                     ts = int(ts_str)
                     # 7 дней = 604800 секунд
                     if current_time - ts < 604800:
                         bad_hashes.add(h)
                         loaded_count += 1
                     else:
                         expired_count += 1
                except ValueError:
                     pass
    except Exception as e:
        logger.warning("Не удалось загрузить кэш плохих конфигов: %s", e)

    if expired_count:
        logger.info("Удалено устаревших плохих конфигов из кэша: %d", expired_count)
    if loaded_count:
        logger.info("Загружено активных плохих конфигов из кэша: %d", loaded_count)
    return bad_hashes


def _save_bad_configs(failed_results: list[CheckResult]) -> None:
    """Сохраняет хеши проваленных проверок в кэш с текущим таймстампом, удаляя дубли и старые записи."""
    current_time = int(time.time())
    existing_entries = {}

    # Читаем старый кэш
    if BAD_CONFIGS_FILE.exists():
        try:
            with open(BAD_CONFIGS_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or "," not in line:
                        continue
                    h, ts_str = line.split(",", 1)
                    try:
                        ts = int(ts_str)
                        if current_time - ts < 604800:
                            existing_entries[h] = ts
                    except ValueError:
                        pass
        except Exception:
            pass

    # Добавляем свежие плохие конфиги
    new_added = 0
    for r in failed_results:
        h = _get_clean_uri_hash(r.config.uri)
        if h not in existing_entries:
            new_added += 1
        existing_entries[h] = current_time

    # Перезаписываем кэш
    try:
        with open(BAD_CONFIGS_FILE, "w", encoding="utf-8") as f:
            for h, ts in existing_entries.items():
                f.write(f"{h},{ts}\n")
        logger.info("Обновлен кэш плохих конфигов: всего в кэше %d (+%d новых)", len(existing_entries), new_added)
    except Exception as e:
        logger.warning("Не удалось сохранить кэш плохих конфигов: %s", e)


def _prepare_output_files() -> None:
    """Очищает выходные файлы и пишет начальные заголовки."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    for path, title in [(ALL_FILE, "ALL"), (SAFE_FILE, "SAFE"), (WHITE_FILE, "WHITE")]:
        with open(path, "w", encoding="utf-8") as f:
            f.write(
                f"# VPN Configs — {title} pool\n"
                f"# Updated: {timestamp}\n"
                f"# Checked via HTTP GET through xray SOCKS5 proxy\n"
                f"# (Writing on the fly)\n"
                f"#\n"
            )


async def _write_output(results: list[CheckResult]) -> tuple[int, int]:
    """
    Записывает живые конфиги в текстовые подписки (перезаписывает файлы отсортированной версией).
    Возвращает (safe_count, white_count).
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    alive_results = [r for r in results if r.alive]

    # Сортируем по latency (быстрые — в начало)
    alive_results.sort(key=lambda r: r.latency_ms or 99999)

    safe_uris = [r.config.uri for r in alive_results if r.config.pool == "safe"]
    white_uris = [r.config.uri for r in alive_results if r.config.pool == "white"]
    all_uris = [r.config.uri for r in alive_results]

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Пишем safe.txt
    header_safe = (
        f"# VPN Configs — SAFE pool\n"
        f"# Updated: {timestamp}\n"
        f"# Total: {len(safe_uris)} configs\n"
        f"# Checked via HTTP GET through xray SOCKS5 proxy\n"
        "#\n"
    )
    with open(SAFE_FILE, "w", encoding="utf-8") as f:
        f.write(header_safe)
        for uri in safe_uris:
            f.write(uri + "\n")

    # Пишем safe_mobile.txt
    safe_mobile = safe_uris[:100]
    header_safe_mobile = (
        f"# VPN Configs — SAFE pool (Mobile, TOP-100)\n"
        f"# Updated: {timestamp}\n"
        f"# Total: {len(safe_mobile)} configs\n"
        f"# Checked via HTTP GET through xray SOCKS5 proxy\n"
        "#\n"
    )
    with open(SAFE_MOBILE_FILE, "w", encoding="utf-8") as f:
        f.write(header_safe_mobile)
        for uri in safe_mobile:
            f.write(uri + "\n")

    # Пишем white.txt
    header_white = (
        f"# VPN Configs — WHITE pool\n"
        f"# Updated: {timestamp}\n"
        f"# Total: {len(white_uris)} configs\n"
        f"# Checked via HTTP GET through xray SOCKS5 proxy\n"
        "#\n"
    )
    with open(WHITE_FILE, "w", encoding="utf-8") as f:
        f.write(header_white)
        for uri in white_uris:
            f.write(uri + "\n")

    # Пишем white_mobile.txt
    white_mobile = white_uris[:100]
    header_white_mobile = (
        f"# VPN Configs — WHITE pool (Mobile, TOP-100)\n"
        f"# Updated: {timestamp}\n"
        f"# Total: {len(white_mobile)} configs\n"
        f"# Checked via HTTP GET through xray SOCKS5 proxy\n"
        "#\n"
    )
    with open(WHITE_MOBILE_FILE, "w", encoding="utf-8") as f:
        f.write(header_white_mobile)
        for uri in white_mobile:
            f.write(uri + "\n")

    # Пишем all.txt
    header_all = (
        f"# VPN Configs — ALL pool\n"
        f"# Updated: {timestamp}\n"
        f"# Total: {len(all_uris)} configs\n"
        f"# Checked via HTTP GET through xray SOCKS5 proxy\n"
        "#\n"
    )
    with open(ALL_FILE, "w", encoding="utf-8") as f:
        f.write(header_all)
        for uri in all_uris:
            f.write(uri + "\n")

    logger.info("Файлы успешно записаны в output/")
    return len(safe_uris), len(white_uris)


def _git_push(safe_count: int, white_count: int) -> None:
    """Делает git add → commit → push."""
    repo_dir = Path(__file__).parent

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    commit_msg = (
        f"chore: update configs [{timestamp}] "
        f"safe={safe_count} white={white_count}"
    )

    cmds = [
        ["git", "add", "output/"],
        ["git", "commit", "-m", commit_msg],
        ["git", "push"],
    ]

    for cmd in cmds:
        logger.info("$ %s", " ".join(cmd))
        result = subprocess.run(
            cmd,
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            # Если "nothing to commit" — не ошибка
            if "nothing to commit" in result.stdout or "nothing to commit" in result.stderr:
                logger.info("Нет изменений для коммита")
                break
            logger.error("Ошибка: %s", result.stderr.strip() or result.stdout.strip())
            raise RuntimeError(f"git команда завершилась с ошибкой: {cmd}")
        if result.stdout.strip():
            logger.info("%s", result.stdout.strip())


async def _run(args: argparse.Namespace) -> None:
    # ── 1. Collect ───────────────────────────────────────────────────────
    logger.info("═══ ЭТАП 1: Сбор конфигов ═══")
    configs: list[Config] = await collect(args.sources)

    if args.limit:
        configs = configs[: args.limit]
        logger.info("Ограничение: используем первые %d конфигов", args.limit)

    if not configs:
        logger.error("Не собрано ни одного конфига!")
        sys.exit(1)

    # Загружаем кэш плохих конфигов и отфильтровываем их
    bad_hashes = _load_bad_configs()
    if bad_hashes:
        filtered_configs = []
        skipped_count = 0
        for c in configs:
            h = _get_clean_uri_hash(c.uri)
            if h in bad_hashes:
                skipped_count += 1
            else:
                filtered_configs.append(c)
        logger.info("Пропущено ранее неработающих конфигов по кэшу: %d", skipped_count)
        configs = filtered_configs

    safe_raw = sum(1 for c in configs if c.pool == "safe")
    white_raw = sum(1 for c in configs if c.pool == "white")
    logger.info(
        "Собрано для проверки: %d конфигов (safe=%d, white=%d)",
        len(configs), safe_raw, white_raw,
    )

    if args.collect_only:
        logger.info("Режим --collect-only, выходим")
        return

    # Очищаем файлы и готовим заголовки перед проверкой
    _prepare_output_files()

    # Сейфлок для параллельной записи на лету
    write_lock = asyncio.Lock()
    alive_results_list = []

    async def on_alive(result: CheckResult):
        nonlocal alive_results_list
        # Обогащаем эмодзи-флагом
        try:
            result.config.uri = await enrich_config_with_country(result.config.uri)
        except Exception:
            pass

        alive_results_list.append(result)

        # Пишем в файлы на лету
        async with write_lock:
            try:
                with open(ALL_FILE, "a", encoding="utf-8") as f:
                    f.write(result.config.uri + "\n")
                if result.config.pool == "safe":
                    with open(SAFE_FILE, "a", encoding="utf-8") as f:
                        f.write(result.config.uri + "\n")
                elif result.config.pool == "white":
                    with open(WHITE_FILE, "a", encoding="utf-8") as f:
                        f.write(result.config.uri + "\n")
            except Exception as e:
                logger.debug("Ошибка записи на лету: %s", e)

    # ── 2. Check ─────────────────────────────────────────────────────────
    logger.info("═══ ЭТАП 2: Проверка через прокси (%d воркеров) ═══", args.workers)
    results: list[CheckResult] = await check_all(
        configs,
        workers=args.workers,
        progress=True,
        on_alive=on_alive
    )

    # Достаем все результаты, которые провалились (для кэша)
    failed = [r for r in results if not r.alive]
    if failed:
        _save_bad_configs(failed)

    alive = [r for r in results if r.alive]
    if not alive:
        logger.warning("Ни один конфиг не прошёл проверку!")

    # ── 3. Write output (перезаписываем начисто отсортированным по latency списком) ──
    logger.info("═══ ЭТАП 3: Запись результатов ═══")
    safe_count, white_count = await _write_output(results)

    # ── 4. Stats ─────────────────────────────────────────────────────────
    logger.info("══════════════════════════════════════")
    logger.info("ИТОГ:")
    logger.info("  Проверено:  %d конфигов", len(results))
    logger.info("  Живых:      %d (%.1f%%)", len(alive), len(alive) * 100 / max(len(results), 1))
    logger.info("  SAFE живых: %d", safe_count)
    logger.info("  WHITE живых:%d", white_count)
    if alive:
        latencies = [r.latency_ms for r in alive if r.latency_ms]
        if latencies:
            avg_ms = sum(latencies) // len(latencies)
            min_ms = min(latencies)
            max_ms = max(latencies)
            logger.info("  Latency:    avg=%dms min=%dms max=%dms", avg_ms, min_ms, max_ms)
    logger.info("══════════════════════════════════════")

    # ── 4. Git push ──────────────────────────────────────────────────────
    if not args.no_push:
        logger.info("═══ ЭТАП 4: Git push ═══")
        try:
            _git_push(safe_count, white_count)
            logger.info("Push выполнен успешно")
        except Exception as e:
            logger.error("Ошибка push: %s", e)
            logger.info("Файлы записаны локально, push можно выполнить вручную")
    else:
        logger.info("--no-push: пропускаем git push")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="VPN Config Collector & Checker",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--sources",
        default="sources.yaml",
        help="Путь к файлу источников",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=200,
        help="Количество параллельных воркеров",
    )
    parser.add_argument(
        "--collect-only",
        action="store_true",
        help="Только сбор без проверки",
    )
    parser.add_argument(
        "--no-push",
        action="store_true",
        help="Не делать git push после проверки",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Ограничить кол-во конфигов (0 = без ограничений)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Подробный вывод (DEBUG)",
    )

    args = parser.parse_args()
    _setup_logging(args.verbose)

    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        logger.info("Прервано пользователем")
        sys.exit(0)


if __name__ == "__main__":
    main()
