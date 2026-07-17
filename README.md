# VLESS-CHECKED-CONFS

Автоматический сборщик и чекер VPN конфигов (VLESS / VMess / Trojan / Shadowsocks).

Проверка ведётся **не TCP-пингом**, а реальным HTTP GET запросом через каждый конфиг.

[![Update VPN Configs](https://github.com/DenisovPlay/VLESS-CHECKED-CONFS/actions/workflows/update.yml/badge.svg)](https://github.com/DenisovPlay/VLESS-CHECKED-CONFS/actions/workflows/update.yml)

## Пулы конфигов

| Файл | Описание |
|------|----------|
| [`output/safe.txt`](output/safe.txt) | Обычные серверы в разных странах |
| [`output/white.txt`](output/white.txt) | Серверы, работающие при **белом списке РКН** |
| [`output/all.txt`](output/all.txt) | Все живые конфиги вместе |

## Как работает

```
sources.yaml            # список источников (safe / white)
    ↓
collector.py            # скачивает файлы, парсит URI, декодирует base64
    ↓
checker.py              # для каждого конфига:
                        #   1. запускает xray-core с SOCKS5 inbound
                        #   2. GET http://cp.cloudflare.com/ через прокси
                        #   3. если HTTP 200 → конфиг живой
    ↓
output/safe.txt         # живые обычные конфиги (отсортированы по latency)
output/white.txt        # живые whitelist конфиги
```

## Обновление

**Автоматически** — GitHub Actions каждые 6 часов.

**Вручную:**
```bash
pip install -r requirements.txt
python main.py
```

## Параметры запуска

```
python main.py --help

  --workers N       Кол-во параллельных воркеров (default: 50)
  --no-push         Не делать git push
  --collect-only    Только сбор без проверки
  --limit N         Проверить только первые N конфигов (тест)
  -v                Подробный вывод
```

## Источники

| Источник | Пул |
|----------|-----|
| [igareck/vpn-configs-for-russia](https://github.com/igareck/vpn-configs-for-russia) | WHITE + SAFE |
| [hiztin/VLESS-PO-GRIBI](https://github.com/hiztin/VLESS-PO-GRIBI) | SAFE |
| [AvenCores/goida-vpn-configs](https://github.com/AvenCores/goida-vpn-configs) | SAFE |
| [mahdibland/V2RayAggregator](https://github.com/mahdibland/V2RayAggregator) | SAFE |
| [barry-far/V2ray-Configs](https://github.com/barry-far/V2ray-Configs) | SAFE |
| [soroushmirzaei/telegram-configs-collector](https://github.com/soroushmirzaei/telegram-configs-collector) | SAFE |
| [peasoft/NoMoreWalls](https://github.com/peasoft/NoMoreWalls) | SAFE |
| ... и другие | SAFE |

## Технический стек

- **xray-core** — VPN клиент (автоскачивается при первом запуске)
- **Python 3.11+** + asyncio — параллельная проверка
- **httpx[socks]** — HTTP через SOCKS5 прокси
- **GitHub Actions** — автозапуск по расписанию
