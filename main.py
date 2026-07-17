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
from datetime import datetime, timezone
from pathlib import Path

import base64
from checker import CheckResult, check_all
from collector import Config, collect
from country_helper import enrich_config_with_country

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).parent / "output"
SAFE_FILE = OUTPUT_DIR / "safe.txt"
WHITE_FILE = OUTPUT_DIR / "white.txt"
ALL_FILE = OUTPUT_DIR / "all.txt"


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


# Файлы вывода
SAFE_FILE = OUTPUT_DIR / "safe.txt"
SAFE_MOBILE_FILE = OUTPUT_DIR / "safe_mobile.txt"
SAFE_B64_FILE = OUTPUT_DIR / "safe_base64.txt"
SAFE_MOBILE_B64_FILE = OUTPUT_DIR / "safe_mobile_base64.txt"

WHITE_FILE = OUTPUT_DIR / "white.txt"
WHITE_MOBILE_FILE = OUTPUT_DIR / "white_mobile.txt"
WHITE_B64_FILE = OUTPUT_DIR / "white_base64.txt"
WHITE_MOBILE_B64_FILE = OUTPUT_DIR / "white_mobile_base64.txt"

ALL_FILE = OUTPUT_DIR / "all.txt"
ALL_MOBILE_FILE = OUTPUT_DIR / "all_mobile.txt"
ALL_B64_FILE = OUTPUT_DIR / "all_base64.txt"
ALL_MOBILE_B64_FILE = OUTPUT_DIR / "all_mobile_base64.txt"


async def _write_output(results: list[CheckResult]) -> tuple[int, int]:
    """
    Записывает живые конфиги в текстовые и base64 подписки.
    Возвращает (safe_count, white_count).
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    alive_results = [r for r in results if r.alive]

    # Сортируем по latency (быстрые — в начало)
    alive_results.sort(key=lambda r: r.latency_ms or 99999)

    # 1. Параллельно обогащаем живые конфиги эмодзи флагами стран
    logger.info("Определяем страны и добавляем эмодзи флаги в имена серверов...")
    async def _enrich(r: CheckResult):
        try:
            r.config.uri = await enrich_config_with_country(r.config.uri)
        except Exception as e:
            logger.debug("Не удалось добавить флаг к %s: %s", r.config.uri[:50], e)

    await asyncio.gather(*(_enrich(r) for r in alive_results))

    safe_uris = [r.config.uri for r in alive_results if r.config.pool == "safe"]
    white_uris = [r.config.uri for r in alive_results if r.config.pool == "white"]
    all_uris = [r.config.uri for r in alive_results]

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def _write_files(
        plain_path: Path,
        mobile_path: Path,
        b64_path: Path,
        mobile_b64_path: Path,
        uris: list[str],
        pool_name: str
    ) -> None:
        # Полная текстовая подписка
        header = (
            f"# VPN Configs — {pool_name} pool\n"
            f"# Updated: {timestamp}\n"
            f"# Total: {len(uris)} configs\n"
            f"# Checked via HTTP GET through xray SOCKS5 proxy\n"
            "#\n"
        )
        with open(plain_path, "w", encoding="utf-8") as f:
            f.write(header)
            for uri in uris:
                f.write(uri + "\n")

        # Мобильная текстовая подписка (ТОП-100)
        mobile_uris = uris[:100]
        header_mobile = (
            f"# VPN Configs — {pool_name} pool (Mobile, TOP-100)\n"
            f"# Updated: {timestamp}\n"
            f"# Total: {len(mobile_uris)} configs\n"
            f"# Checked via HTTP GET through xray SOCKS5 proxy\n"
            "#\n"
        )
        with open(mobile_path, "w", encoding="utf-8") as f:
            f.write(header_mobile)
            for uri in mobile_uris:
                f.write(uri + "\n")

        # Base64 подписки (без комментариев, чистые URI)
        plain_b64_content = base64.b64encode(("\n".join(uris) + "\n").encode("utf-8")).decode("utf-8")
        with open(b64_path, "w", encoding="utf-8") as f:
            f.write(plain_b64_content)

        mobile_b64_content = base64.b64encode(("\n".join(mobile_uris) + "\n").encode("utf-8")).decode("utf-8")
        with open(mobile_b64_path, "w", encoding="utf-8") as f:
            f.write(mobile_b64_content)

        logger.info("Записаны файлы %s (%d) и %s (%d)", plain_path.name, len(uris), mobile_path.name, len(mobile_uris))

    _write_files(SAFE_FILE, SAFE_MOBILE_FILE, SAFE_B64_FILE, SAFE_MOBILE_B64_FILE, safe_uris, "SAFE")
    _write_files(WHITE_FILE, WHITE_MOBILE_FILE, WHITE_B64_FILE, WHITE_MOBILE_B64_FILE, white_uris, "WHITE")
    _write_files(ALL_FILE, ALL_MOBILE_FILE, ALL_B64_FILE, ALL_MOBILE_B64_FILE, all_uris, "ALL")

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

    safe_raw = sum(1 for c in configs if c.pool == "safe")
    white_raw = sum(1 for c in configs if c.pool == "white")
    logger.info(
        "Собрано: %d конфигов (safe=%d, white=%d)",
        len(configs), safe_raw, white_raw,
    )

    if args.collect_only:
        logger.info("Режим --collect-only, выходим")
        return

    # ── 2. Check ─────────────────────────────────────────────────────────
    logger.info("═══ ЭТАП 2: Проверка через прокси (%d воркеров) ═══", args.workers)
    results: list[CheckResult] = await check_all(
        configs,
        workers=args.workers,
        progress=True,
    )

    alive = [r for r in results if r.alive]
    if not alive:
        logger.warning("Ни один конфиг не прошёл проверку!")

    # ── 3. Write output ──────────────────────────────────────────────────
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
        default=50,
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
