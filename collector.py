"""
collector.py — скачивает конфиги из всех источников sources.yaml,
парсит URI (vless://, vmess://, trojan://, ss://) и возвращает
List[dict] с полями: uri, pool, source_url
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

import httpx
import yaml

logger = logging.getLogger(__name__)

SUPPORTED_SCHEMES = ("vless://", "vmess://", "trojan://", "ss://")

# Паттерн для вытаскивания URI из текста
_URI_RE = re.compile(
    r"(?:vless|vmess|trojan|ss)://[A-Za-z0-9+/=@:\[\].?&#%_\-]+"
)


@dataclass
class Config:
    uri: str
    pool: str           # "safe" | "white"
    source_url: str


def _extract_uris(text: str) -> list[str]:
    """Вытаскивает все VPN URI из произвольного текста."""
    return _URI_RE.findall(text)


def _try_base64_decode(data: str) -> str | None:
    """Пробует декодировать base64. Возвращает None если не base64."""
    # Убираем whitespace
    stripped = data.strip().replace("\n", "").replace("\r", "")
    # Дополняем padding
    pad = 4 - len(stripped) % 4
    if pad != 4:
        stripped += "=" * pad
    try:
        decoded = base64.b64decode(stripped).decode("utf-8", errors="replace")
        # Если после декода нашлись URI — это точно base64-подписка
        if any(scheme in decoded for scheme in SUPPORTED_SCHEMES):
            return decoded
    except Exception:
        pass
    return None


def _parse_content(content: str) -> list[str]:
    """
    Парсит содержимое файла:
    1. Сначала пробует целиком как base64-подписку
    2. Затем ищет URI построчно (каждая строка тоже может быть base64)
    3. Затем regex-поиск по всему тексту
    """
    uris: list[str] = []

    # Попытка 1: весь файл как base64
    decoded_whole = _try_base64_decode(content)
    if decoded_whole:
        uris.extend(_extract_uris(decoded_whole))
        if uris:
            return list(dict.fromkeys(uris))  # deduplicate, preserve order

    # Попытка 2: построчно
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Строка сама по себе URI?
        if any(line.startswith(s) for s in SUPPORTED_SCHEMES):
            uris.append(line)
            continue
        # Строка может быть base64-encoded URI?
        decoded_line = _try_base64_decode(line)
        if decoded_line:
            uris.extend(_extract_uris(decoded_line))
            continue

    # Попытка 3: regex по всему тексту (если поймали мало)
    if len(uris) < 5:
        regex_uris = _extract_uris(content)
        if regex_uris:
            uris.extend(regex_uris)

    # Дедупликация
    return list(dict.fromkeys(uris))


async def _fetch(
    client: httpx.AsyncClient,
    url: str,
    semaphore: asyncio.Semaphore,
    retries: int = 2,
) -> str | None:
    """Скачивает URL с ограничением concurrency и retry."""
    async with semaphore:
        for attempt in range(retries + 1):
            try:
                resp = await client.get(url, timeout=30, follow_redirects=True)
                resp.raise_for_status()
                return resp.text
            except Exception as exc:
                if attempt < retries:
                    await asyncio.sleep(1.0 * (attempt + 1))
                    continue
                logger.warning("Не удалось скачать %s: %s", url, exc)
                return None
    return None


async def collect(sources_file: str | Path = "sources.yaml") -> list[Config]:
    """
    Главная функция: скачивает все источники и возвращает список Config.
    """
    sources_path = Path(sources_file)
    if not sources_path.exists():
        raise FileNotFoundError(f"Файл источников не найден: {sources_path}")

    with open(sources_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    sources = data.get("sources", [])
    logger.info("Загружено источников: %d", len(sources))

    all_configs: list[Config] = []

    # Ограничиваем параллелизм сборщика — GitHub rate-limits при 60+ одновременных запросах
    fetch_semaphore = asyncio.Semaphore(10)
    async with httpx.AsyncClient(
        headers={"User-Agent": "Mozilla/5.0 (vpn-checker/1.0)"},
        timeout=30,
        follow_redirects=True,
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
    ) as client:
        tasks = [_fetch(client, s["url"], fetch_semaphore) for s in sources]
        results = await asyncio.gather(*tasks)

    for source, content in zip(sources, results):
        if content is None:
            continue
        pool = source.get("pool", "safe")
        url = source["url"]
        uris = _parse_content(content)
        logger.info("  %s → %d конфигов [%s]", url.split("/")[-1], len(uris), pool)
        for uri in uris:
            all_configs.append(Config(uri=uri, pool=pool, source_url=url))

    # Глобальная дедупликация по URI (один конфиг может быть в нескольких источниках)
    seen: set[str] = set()
    unique: list[Config] = []
    for cfg in all_configs:
        if cfg.uri not in seen:
            seen.add(cfg.uri)
            unique.append(cfg)

    logger.info("Итого уникальных конфигов: %d", len(unique))
    return unique


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    configs = asyncio.run(collect())
    print(f"Собрано: {len(configs)} конфигов")
    safe_count = sum(1 for c in configs if c.pool == "safe")
    white_count = sum(1 for c in configs if c.pool == "white")
    print(f"  SAFE:  {safe_count}")
    print(f"  WHITE: {white_count}")
