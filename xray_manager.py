"""
xray_manager.py — управление бинарником xray-core:
- Автоматическая загрузка последнего релиза с GitHub
- Генерация JSON-конфигов для каждого VPN-протокола
- Парсинг URI (vless://, vmess://, trojan://, ss://)
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import platform
import re
import stat
import subprocess
import tempfile
import urllib.parse
import zipfile
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Где хранить скачанный xray бинарник
XRAY_DIR = Path(__file__).parent / ".xray"
XRAY_RELEASES_URL = "https://api.github.com/repos/XTLS/Xray-core/releases/latest"


def _get_xray_platform() -> str:
    """Определяем правильное имя архива для текущей платформы."""
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "linux":
        if machine in ("x86_64", "amd64"):
            return "Xray-linux-64.zip"
        elif machine in ("aarch64", "arm64"):
            return "Xray-linux-arm64-v8a.zip"
        elif machine.startswith("arm"):
            return "Xray-linux-arm32-v7a.zip"
        elif machine in ("i386", "i686", "x86"):
            return "Xray-linux-32.zip"
    elif system == "darwin":
        if machine in ("arm64", "aarch64"):
            return "Xray-macos-arm64-v8a.zip"
        else:
            return "Xray-macos-64.zip"
    elif system == "windows":
        if machine in ("arm64", "aarch64"):
            return "Xray-windows-arm64-v8a.zip"
        elif machine in ("x86_64", "amd64"):
            return "Xray-windows-64.zip"
        else:
            return "Xray-windows-32.zip"

    raise RuntimeError(f"Unsupported platform: {system}/{machine}")


async def ensure_xray() -> Path:
    """
    Убеждаемся что xray доступен в локальной директории .xray/.
    Если нет — скачиваем.
    Returns: Path к исполняемому файлу xray.
    """
    # Проверяем в .xray/
    xray_bin = XRAY_DIR / ("xray.exe" if platform.system() == "Windows" else "xray")
    if xray_bin.exists():
        logger.info("Используем локальный xray: %s", xray_bin)
        return xray_bin

    # Скачиваем
    logger.info("Скачиваем xray-core...")
    await _download_xray(xray_bin)
    return xray_bin


async def _download_xray(target_bin: Path) -> None:
    """Скачивает последний релиз xray-core с GitHub."""
    target_bin.parent.mkdir(parents=True, exist_ok=True)
    platform_zip = _get_xray_platform()

    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        # Получаем URL последнего релиза
        resp = await client.get(XRAY_RELEASES_URL)
        resp.raise_for_status()
        release = resp.json()
        version = release["tag_name"]
        logger.info("Скачиваем xray %s (%s)...", version, platform_zip)

        # Ищем нужный asset
        download_url = None
        for asset in release["assets"]:
            if asset["name"] == platform_zip:
                download_url = asset["browser_download_url"]
                break

        if not download_url:
            raise RuntimeError(f"Не найден asset {platform_zip} в релизе {version}")

        # Скачиваем zip
        zip_path = target_bin.parent / platform_zip
        async with client.stream("GET", download_url) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            with open(zip_path, "wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = downloaded * 100 // total
                        print(f"\r  Загрузка xray: {pct}%", end="", flush=True)
        print()

    # Распаковываем
    with zipfile.ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            if name in ("xray", "xray.exe"):
                with zf.open(name) as src, open(target_bin, "wb") as dst:
                    dst.write(src.read())
                break
        else:
            raise RuntimeError(f"xray бинарник не найден в {platform_zip}")

    zip_path.unlink()

    # Делаем исполняемым (Unix)
    if platform.system() != "Windows":
        st = os.stat(target_bin)
        os.chmod(target_bin, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    logger.info("xray установлен: %s", target_bin)


# ─── Парсинг URI ──────────────────────────────────────────────────────────────

def _parse_vless(uri: str) -> dict[str, Any] | None:
    """
    vless://uuid@host:port?type=...&security=...&sni=...&flow=...#remark
    """
    try:
        without_scheme = uri[len("vless://"):]
        # Разделяем remark
        if "#" in without_scheme:
            without_scheme, _ = without_scheme.rsplit("#", 1)

        # uuid@host:port?params
        at_idx = without_scheme.index("@")
        uuid = without_scheme[:at_idx]
        rest = without_scheme[at_idx + 1:]

        if "?" in rest:
            hostport, params_str = rest.split("?", 1)
        else:
            hostport, params_str = rest, ""

        # IPv6 support
        if hostport.startswith("["):
            bracket_end = hostport.index("]")
            host = hostport[1:bracket_end]
            port = int(hostport[bracket_end + 2:])
        else:
            host, port_str = hostport.rsplit(":", 1)
            port = int(port_str)

        params = dict(urllib.parse.parse_qsl(params_str))
        network = params.get("type", "tcp")
        security = params.get("security", "none")

        stream_settings = _build_stream_settings(network, security, params)

        outbound: dict[str, Any] = {
            "protocol": "vless",
            "settings": {
                "vnext": [{
                    "address": host,
                    "port": port,
                    "users": [{
                        "id": uuid,
                        "encryption": "none",
                        "flow": params.get("flow", ""),
                    }]
                }]
            },
            "streamSettings": stream_settings,
        }
        return outbound
    except Exception as e:
        logger.debug("Не удалось парсить vless URI: %s — %s", uri[:80], e)
        return None


def _parse_vmess(uri: str) -> dict[str, Any] | None:
    """
    vmess://base64(json)
    """
    try:
        b64 = uri[len("vmess://"):]
        # Нормализуем padding
        pad = 4 - len(b64) % 4
        if pad != 4:
            b64 += "=" * pad
        data = json.loads(base64.b64decode(b64).decode("utf-8"))

        host = data.get("add", "")
        port = int(data.get("port", 443))
        uuid = data.get("id", "")
        alter_id = int(data.get("aid", 0))
        network = data.get("net", "tcp")
        security = data.get("tls", "none")

        params = {
            "type": network,
            "security": security,
            "sni": data.get("sni", data.get("host", "")),
            "host": data.get("host", ""),
            "path": data.get("path", "/"),
            "headerType": data.get("type", ""),
        }
        stream_settings = _build_stream_settings(network, security, params)

        outbound: dict[str, Any] = {
            "protocol": "vmess",
            "settings": {
                "vnext": [{
                    "address": host,
                    "port": port,
                    "users": [{
                        "id": uuid,
                        "alterId": alter_id,
                        "security": data.get("scy", "auto"),
                    }]
                }]
            },
            "streamSettings": stream_settings,
        }
        return outbound
    except Exception as e:
        logger.debug("Не удалось парсить vmess URI: %s — %s", uri[:80], e)
        return None


def _parse_trojan(uri: str) -> dict[str, Any] | None:
    """
    trojan://password@host:port?sni=...&type=...#remark
    """
    try:
        without_scheme = uri[len("trojan://"):]
        if "#" in without_scheme:
            without_scheme, _ = without_scheme.rsplit("#", 1)

        at_idx = without_scheme.index("@")
        password = urllib.parse.unquote(without_scheme[:at_idx])
        rest = without_scheme[at_idx + 1:]

        if "?" in rest:
            hostport, params_str = rest.split("?", 1)
        else:
            hostport, params_str = rest, ""

        if hostport.startswith("["):
            bracket_end = hostport.index("]")
            host = hostport[1:bracket_end]
            port = int(hostport[bracket_end + 2:])
        else:
            host, port_str = hostport.rsplit(":", 1)
            port = int(port_str)

        params = dict(urllib.parse.parse_qsl(params_str))
        network = params.get("type", "tcp")
        security = params.get("security", "tls")

        stream_settings = _build_stream_settings(network, security, params)

        outbound: dict[str, Any] = {
            "protocol": "trojan",
            "settings": {
                "servers": [{
                    "address": host,
                    "port": port,
                    "password": password,
                }]
            },
            "streamSettings": stream_settings,
        }
        return outbound
    except Exception as e:
        logger.debug("Не удалось парсить trojan URI: %s — %s", uri[:80], e)
        return None


def _parse_ss(uri: str) -> dict[str, Any] | None:
    """
    ss://base64(method:password)@host:port#remark
    или
    ss://method:password@host:port#remark
    """
    try:
        without_scheme = uri[len("ss://"):]
        if "#" in without_scheme:
            without_scheme, _ = without_scheme.rsplit("#", 1)

        # SIP002 формат: ss://userinfo@host:port
        if "@" in without_scheme:
            userinfo_b64, hostport = without_scheme.rsplit("@", 1)
            try:
                # SIP002: userinfo = base64(method:password) или plain method:password
                pad = 4 - len(userinfo_b64) % 4
                if pad != 4:
                    userinfo_b64 += "=" * pad
                userinfo = base64.b64decode(userinfo_b64).decode()
                method, password = userinfo.split(":", 1)
            except Exception:
                # Попробуем как plain text
                userinfo = urllib.parse.unquote(userinfo_b64)
                method, password = userinfo.split(":", 1)
        else:
            # Старый формат: ss://base64(method:password@host:port)
            pad = 4 - len(without_scheme) % 4
            if pad != 4:
                without_scheme += "=" * pad
            decoded = base64.b64decode(without_scheme).decode()
            at_idx = decoded.rindex("@")
            userinfo = decoded[:at_idx]
            hostport = decoded[at_idx + 1:]
            method, password = userinfo.split(":", 1)

        # Парсим host:port
        hostport = hostport.split("?")[0]  # убираем параметры
        if hostport.startswith("["):
            bracket_end = hostport.index("]")
            host = hostport[1:bracket_end]
            port = int(hostport[bracket_end + 2:])
        else:
            host, port_str = hostport.rsplit(":", 1)
            port = int(port_str)

        outbound: dict[str, Any] = {
            "protocol": "shadowsocks",
            "settings": {
                "servers": [{
                    "address": host,
                    "port": port,
                    "method": method,
                    "password": password,
                    "ota": False,
                }]
            },
            "streamSettings": {"network": "tcp"},
        }
        return outbound
    except Exception as e:
        logger.debug("Не удалось парсить ss URI: %s — %s", uri[:80], e)
        return None


def _build_stream_settings(
    network: str, security: str, params: dict[str, str]
) -> dict[str, Any]:
    """Строит streamSettings для xray конфига."""
    ss: dict[str, Any] = {"network": network}

    # TLS / Reality
    if security in ("tls", "reality"):
        sni = params.get("sni") or params.get("host") or ""
        tls_cfg: dict[str, Any] = {
            "serverName": sni,
            "allowInsecure": True,
            "fingerprint": params.get("fp", "chrome"),
        }
        if security == "reality":
            tls_cfg["publicKey"] = params.get("pbk", "")
            tls_cfg["shortId"] = params.get("sid", "")
            tls_cfg["spiderX"] = params.get("spx", "/")
            ss["realitySettings"] = tls_cfg
            ss["security"] = "reality"
        else:
            ss["tlsSettings"] = tls_cfg
            ss["security"] = "tls"

    # Transport-specific
    if network == "ws":
        ss["wsSettings"] = {
            "path": params.get("path", "/"),
            "headers": {"Host": params.get("host", "")},
        }
    elif network == "grpc":
        ss["grpcSettings"] = {
            "serviceName": params.get("serviceName", params.get("path", "")),
        }
    elif network == "h2":
        ss["httpSettings"] = {
            "path": params.get("path", "/"),
            "host": [params.get("host", "")],
        }
    elif network == "tcp":
        header_type = params.get("headerType", "none")
        if header_type == "http":
            ss["tcpSettings"] = {
                "header": {
                    "type": "http",
                    "request": {
                        "path": [params.get("path", "/")],
                        "headers": {"Host": [params.get("host", "")]},
                    }
                }
            }

    return ss


def parse_uri_to_outbound(uri: str) -> dict[str, Any] | None:
    """Парсит любой VPN URI и возвращает xray outbound dict."""
    if uri.startswith("vless://"):
        return _parse_vless(uri)
    elif uri.startswith("vmess://"):
        return _parse_vmess(uri)
    elif uri.startswith("trojan://"):
        return _parse_trojan(uri)
    elif uri.startswith("ss://"):
        return _parse_ss(uri)
    return None


def build_xray_config(outbound: dict[str, Any], socks_port: int) -> dict[str, Any]:
    """
    Строит полный JSON-конфиг для xray с SOCKS5 inbound и заданным outbound.
    """
    return {
        "log": {"loglevel": "none"},
        "inbounds": [
            {
                "tag": "socks-in",
                "port": socks_port,
                "listen": "127.0.0.1",
                "protocol": "socks",
                "settings": {
                    "auth": "noauth",
                    "udp": False,
                },
            }
        ],
        "outbounds": [
            {**outbound, "tag": "proxy-out"}
        ]
    }
