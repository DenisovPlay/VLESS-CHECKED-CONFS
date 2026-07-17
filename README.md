# VLESS-CHECKED-CONFS

Автоматический сборщик и чекер VPN конфигов (VLESS / VMess / Trojan / Shadowsocks).

Проверка ведётся **не TCP-пингом**, а реальным HTTP GET запросом через каждый конфиг.

[![Update VPN Configs](https://github.com/DenisovPlay/VLESS-CHECKED-CONFS/actions/workflows/update.yml/badge.svg)](https://github.com/DenisovPlay/VLESS-CHECKED-CONFS/actions/workflows/update.yml)

## Пулы конфигов и подписки

Все файлы подписок представляют собой простой список серверов (один URI на строку). Этот формат поддерживается всеми современными клиентами на iOS, Android, Windows и macOS.

| Подписка | Пул | Прямая RAW ссылка для приложения |
|---|---|---|
| **ALL** | Все проверенные сервера (микс) | `https://raw.githubusercontent.com/DenisovPlay/VLESS-CHECKED-CONFS/main/output/all.txt` |
| **SAFE** | Обычные сервера в разных странах | `https://raw.githubusercontent.com/DenisovPlay/VLESS-CHECKED-CONFS/main/output/safe.txt` |
| **WHITE** | Сервера, работающие при **белом списке РКН** | `https://raw.githubusercontent.com/DenisovPlay/VLESS-CHECKED-CONFS/main/output/white.txt` |
| **SAFE Mobile** | ТОП-100 лучших обычных серверов по latency | `https://raw.githubusercontent.com/DenisovPlay/VLESS-CHECKED-CONFS/main/output/safe_mobile.txt` |
| **WHITE Mobile** | ТОП-100 лучших серверов для белого списка РКН | `https://raw.githubusercontent.com/DenisovPlay/VLESS-CHECKED-CONFS/main/output/white_mobile.txt` |

> [!TIP]
> **Для телефонов рекомендуется использовать версии `safe_mobile.txt` или `white_mobile.txt`**, чтобы клиент не зависал от перегрузки тысячами конфигураций, а работал только со 100 самыми быстрыми серверами.

---

## 📲 Как добавить в приложения (Happ, v2rayNG, Nekobox, Streisand)

Все эти подписки полностью совместимы с популярными VPN-клиентами.

### 1. Happ (iOS / Android)
- Скопируйте одну из ссылок выше (например, `safe_mobile.txt`).
- Откройте приложение **Happ**.
- Перейдите в раздел настройки подписок / добавить новый ресурс.
- Вставьте ссылку и нажмите "Обновить".
- Приложение автоматически скачает сервера, добавив в имена красивые эмодзи флагов стран.

### 2. v2rayNG (Android)
- Скопируйте нужную ссылку.
- Откройте меню слева → **Группы подписок** (Subscription grouping).
- Нажмите `+` в правом верхнем углу, введите любое имя и вставьте скопированную ссылку.
- Сохраните, затем на главном экране нажмите три точки вверху → **Обновить подписку** (Update subscription).

### 3. Nekobox (Android / Windows) / Streisand (iOS)
- Скопируйте ссылку.
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
