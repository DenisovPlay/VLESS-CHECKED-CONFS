"""
country_helper.py — определение страны по ремарке или IP/хосту конфига,
добавление флага (эмодзи) страны в имя/ремарку конфига.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import socket
import urllib.parse
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Кеш геолокации IP: {host_or_ip: country_code}
_GEOLOCATE_CACHE: dict[str, str] = {}

# Список известных стран в именах серверов
COUNTRY_KEYWORDS = {
    # Код страны -> список ключевых слов (регистронезависимо)
    "RU": ["ru", "russia", "россия", "rus", "ru-all", "ru-checked", "sni-ru", "cidr-ru"],
    "US": ["us", "usa", "united states", "сша", "america", "united-states"],
    "DE": ["de", "germany", "германия", "deutschland", "de-"],
    "NL": ["nl", "netherlands", "нидерланды", "holland", "nl-"],
    "FI": ["fi", "finland", "финляндия", "hels", "helsinki"],
    "SE": ["se", "sweden", "швеция", "stockholm"],
    "GB": ["gb", "uk", "united kingdom", "великобритания", "london", "fareham"],
    "FR": ["fr", "france", "франция", "paris"],
    "KZ": ["kz", "kazakhstan", "казахстан", "almaty"],
    "BY": ["by", "belarus", "беларусь", "белоруссия", "minsk"],
    "UA": ["ua", "ukraine", "украина", "kyiv", "kiev"],
    "SG": ["sg", "singapore", "сингапур"],
    "JP": ["jp", "japan", "япония", "tokyo"],
    "HK": ["hk", "hong kong", "гонконг", "hong-kong"],
    "TR": ["tr", "turkey", "турция", "istanbul"],
    "PL": ["pl", "poland", "польша", "warsaw"],
    "CH": ["ch", "switzerland", "швейцария"],
    "AT": ["at", "austria", "австрия", "vienna"],
    "BG": ["bg", "bulgaria", "болгария", "sofia"],
    "ES": ["es", "spain", "испания", "madrid"],
    "IT": ["it", "italy", "италия", "milan", "rome"],
    "CA": ["ca", "canada", "канада", "toronto", "montreal"],
    "KR": ["kr", "korea", "корея", "seoul"],
    "AE": ["ae", "uae", "dubai", "оаэ"],
    "IN": ["in", "india", "индия", "mumbai"],
    "CN": ["cn", "china", "китай"],
}


def country_code_to_emoji(country_code: str) -> str:
    """Конвертирует двухбуквенный код страны (e.g. RU, US) в эмодзи флаг."""
    if not country_code or len(country_code) != 2:
        return ""
    try:
        return "".join(chr(127397 + ord(c)) for c in country_code.upper())
    except Exception:
        return ""


def has_emoji(text: str) -> bool:
    """Проверяет, содержит ли текст флаг страны или другие эмодзи."""
    for char in text:
        # Диапазоны для региональных флагов (0x1F1E6 - 0x1F1FF) и общих эмодзи
        if 0x1F1E6 <= ord(char) <= 0x1F1FF or 0x1F600 <= ord(char) <= 0x1F9FF or 0x1F300 <= ord(char) <= 0x1F5FF:
            return True
    return False


def detect_country_from_remark(remark: str) -> str | None:
    """
    Пытается определить страну по тексту ремарки.
    Возвращает двухбуквенный код страны (e.g. 'RU') или None.
    """
    if not remark:
        return None

    # Очищаем ремарку для анализа слов
    cleaned = re.sub(r"[^\w\s\-\|]", " ", remark.lower())
    words = set(cleaned.split())

    # 1. Сначала ищем точное соответствие кодов стран (RU, US, DE и т.д.)
    # Но исключаем ложные срабатывания (например, 'is', 'in' и т.д.)
    for code, keywords in COUNTRY_KEYWORDS.items():
        # Если код страны присутствует как отдельное слово/префикс
        if code.lower() in words:
            return code

    # 2. Ищем ключевые слова (например, 'germany', 'finland')
    for code, keywords in COUNTRY_KEYWORDS.items():
        for kw in keywords:
            if kw in remark.lower():
                return code

    return None


async def geolocate_host(host: str) -> str | None:
    """
    Разрешает хост в IP и определяет страну через freeipapi API.
    Использует внутренний кэш для предотвращения повторных запросов.
    """
    if not host:
        return None

    # 1. Проверяем кэш
    if host in _GEOLOCATE_CACHE:
        return _GEOLOCATE_CACHE[host]

    # 2. Проверяем, является ли хост IP адресом, или резолвим его
    ip = host
    is_ip = re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", host)
    if not is_ip:
        try:
            # Делаем неблокирующий DNS-резолв
            loop = asyncio.get_event_loop()
            ip = await loop.run_in_executor(None, socket.gethostbyname, host)
        except Exception:
            logger.debug("Не удалось разрешить хост %s", host)
            return None

    # Проверяем кэш по IP
    if ip in _GEOLOCATE_CACHE:
        _GEOLOCATE_CACHE[host] = _GEOLOCATE_CACHE[ip]
        return _GEOLOCATE_CACHE[ip]

    # 3. Делаем запрос к API
    url = f"https://free.freeipapi.com/api/json/{ip}"
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                code = data.get("countryCode")
                if code and len(code) == 2:
                    _GEOLOCATE_CACHE[host] = code
                    _GEOLOCATE_CACHE[ip] = code
                    return code
    except Exception as e:
        logger.debug("Ошибка геолокации IP %s: %s", ip, e)

    return None


# ─── Разбор и сборка URI с добавлением флага ─────────────────────────────────

def _extract_host_and_remark(uri: str) -> tuple[str | None, str | None]:
    """Возвращает (host, remark_decoded) из URI."""
    try:
        if uri.startswith("vmess://"):
            b64 = uri[len("vmess://"):]
            pad = 4 - len(b64) % 4
            if pad != 4:
                b64 += "=" * pad
            data = json.loads(base64.b64decode(b64).decode("utf-8"))
            return data.get("add"), data.get("ps")

        elif uri.startswith(("vless://", "trojan://", "ss://")):
            # split remark
            remark = None
            without_remark = uri
            if "#" in uri:
                without_remark, remark_raw = uri.rsplit("#", 1)
                remark = urllib.parse.unquote(remark_raw)

            # split host
            # scheme://userinfo@host:port?...
            without_scheme = without_remark.split("://", 1)[1]
            if "@" in without_scheme:
                rest = without_scheme.rsplit("@", 1)[1]
            else:
                rest = without_scheme

            # rest is host:port?params
            hostport = rest.split("?")[0]
            if hostport.startswith("["):
                host = hostport[1:hostport.index("]")]
            else:
                host = hostport.rsplit(":", 1)[0]
            return host, remark
    except Exception:
        pass
    return None, None


def _update_uri_remark(uri: str, new_remark: str) -> str:
    """Заменяет ремарку в URI на новую."""
    try:
        if uri.startswith("vmess://"):
            b64 = uri[len("vmess://"):]
            pad = 4 - len(b64) % 4
            if pad != 4:
                b64 += "=" * pad
            data = json.loads(base64.b64decode(b64).decode("utf-8"))
            data["ps"] = new_remark
            encoded = base64.b64encode(json.dumps(data).encode("utf-8")).decode("utf-8")
            return f"vmess://{encoded}"

        elif uri.startswith(("vless://", "trojan://", "ss://")):
            without_remark = uri.split("#")[0]
            encoded_remark = urllib.parse.quote(new_remark)
            return f"{without_remark}#{encoded_remark}"
    except Exception:
        pass
    return uri


async def enrich_config_with_country(uri: str) -> str:
    """
    Принимает URI конфига, определяет страну (из ремарки или по IP),
    добавляет эмодзи-флаг страны в ремарку и возвращает обновленный URI.
    """
    host, remark = _extract_host_and_remark(uri)
    remark = remark or ""

    # Если в ремарке уже есть флаг/эмодзи, ничего не меняем
    if has_emoji(remark):
        return uri

    # 1. Пытаемся вытянуть страну из названия
    code = detect_country_from_remark(remark)

    # 2. Если не получилось, определяем по IP хоста
    if not code and host:
        code = await geolocate_host(host)

    if code:
        flag = country_code_to_emoji(code)
        if flag:
            # Форматируем новую ремарку: "[Флаг] Исходное имя"
            # Если имя пустое, пишем "[Флаг] VLESS [code]"
            if not remark:
                proto = uri.split("://")[0].upper()
                new_remark = f"{flag} {proto} ({code})"
            else:
                new_remark = f"{flag} {remark}"

            return _update_uri_remark(uri, new_remark)

    # Если не удалось определить страну, оставляем как есть
    return uri
