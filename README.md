# VLESS-CHECKED-CONFS

Автоматический сборщик и чекер VPN конфигов (VLESS / VMess / Trojan / Shadowsocks).

Проверка ведётся **не TCP-пингом**, а реальным HTTP GET запросом через каждый конфиг.

[![Update VPN Configs](https://github.com/DenisovPlay/VLESS-CHECKED-CONFS/actions/workflows/update.yml/badge.svg)](https://github.com/DenisovPlay/VLESS-CHECKED-CONFS/actions/workflows/update.yml)

## Пулы конфигов и подписки

Все подписки доступны в двух форматах:
1. **TXT (Plain)** — простой список серверов (один URI на строку). Подходит для большинства современных клиентов (Streisand, Shadowrocket, Nekobox и др.).
2. **Base64** — стандартный закодированный формат подписки. Требуется для клиентов, которые не умеют читать обычный текст напрямую (например, v2rayNG, Happ и др.).

### 📂 Полные подписки (все рабочие сервера)
| Название | Пул | TXT ссылка | Base64 ссылка |
|---|---|---|---|
| **SAFE** (обычные) | Сервера в разных странах | [`safe.txt`](https://raw.githubusercontent.com/DenisovPlay/VLESS-CHECKED-CONFS/main/output/safe.txt) | [`safe_base64.txt`](https://raw.githubusercontent.com/DenisovPlay/VLESS-CHECKED-CONFS/main/output/safe_base64.txt) |
| **WHITE** (белый список) | Обход белых списков РКН | [`white.txt`](https://raw.githubusercontent.com/DenisovPlay/VLESS-CHECKED-CONFS/main/output/white.txt) | [`white_base64.txt`](https://raw.githubusercontent.com/DenisovPlay/VLESS-CHECKED-CONFS/main/output/white_base64.txt) |
| **ALL** (микс) | Все живые конфиги | [`all.txt`](https://raw.githubusercontent.com/DenisovPlay/VLESS-CHECKED-CONFS/main/output/all.txt) | [`all_base64.txt`](https://raw.githubusercontent.com/DenisovPlay/VLESS-CHECKED-CONFS/main/output/all_base64.txt) |

### 📱 Мобильные подписки (ТОП-100 лучших серверов по latency)
> [!TIP]
> Рекомендуется для мобильных устройств, чтобы клиент не зависал при перегрузке тысячами конфигов.

| Название | Пул | TXT ссылка | Base64 ссылка |
|---|---|---|---|
| **SAFE Mobile** | ТОП-100 обычных | [`safe_mobile.txt`](https://raw.githubusercontent.com/DenisovPlay/VLESS-CHECKED-CONFS/main/output/safe_mobile.txt) | [`safe_mobile_base64.txt`](https://raw.githubusercontent.com/DenisovPlay/VLESS-CHECKED-CONFS/main/output/safe_mobile_base64.txt) |
| **WHITE Mobile** | ТОП-100 для белого списка | [`white_mobile.txt`](https://raw.githubusercontent.com/DenisovPlay/VLESS-CHECKED-CONFS/main/output/white_mobile.txt) | [`white_mobile_base64.txt`](https://raw.githubusercontent.com/DenisovPlay/VLESS-CHECKED-CONFS/main/output/white_mobile_base64.txt) |
| **ALL Mobile** | ТОП-100 микс | [`all_mobile.txt`](https://raw.githubusercontent.com/DenisovPlay/VLESS-CHECKED-CONFS/main/output/all_mobile.txt) | [`all_mobile_base64.txt`](https://raw.githubusercontent.com/DenisovPlay/VLESS-CHECKED-CONFS/main/output/all_mobile_base64.txt) |

---

## 📲 Как добавить в приложения (Happ, v2rayNG, Nekobox и др.)

Все эти подписки полностью совместимы с популярными VPN-клиентами.

### 1. Happ (iOS / Android)
- Скопируйте **Base64 ссылку** нужной вам подписки (например, `safe_mobile_base64.txt`).
- Откройте приложение **Happ**.
- Перейдите в настройки подписок / добавить новый ресурс.
- Вставьте ссылку и нажмите "Обновить".
- Приложение автоматически скачает и расшифрует все сервера, добавив в имена красивые эмодзи флагов стран.

### 2. v2rayNG (Android)
- Скопируйте **Base64 ссылку** (v2rayNG требует строго Base64 формат).
- Откройте меню слева → **Группы подписок** (Subscription grouping).
- Нажмите `+` в правом верхнем углу, введите любое имя и вставьте скопированную URL ссылку.
- Сохраните, затем на главном экране нажмите три точки вверху → **Обновить подписку** (Update subscription).

### 3. Nekobox (Android / Windows) / Streisand (iOS)
- Скопируйте либо **TXT**, либо **Base64 ссылку** (эти приложения поддерживают оба формата).
- В Nekobox: меню → Группы → Добавить подписку → Вставьте URL.
- В Streisand: нажмите `+` → Добавить подписку → Вставьте URL.
- Обновите список.


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
