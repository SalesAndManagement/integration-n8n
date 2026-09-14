# realting-sync — імпорт заявок realting.com у Bitrix24

Сервіс на чистому Python (стандартна бібліотека, без залежностей і venv): раз на 5 хвилин
забирає нові заявки з експорту realting.com, нормалізує їх і створює ліди в Bitrix24.
Запускається systemd-таймером, стан тримає у SQLite.

```
realting.com (api-export)          VPS                              Bitrix24
   ┌──────────────┐        ┌────────────────────┐          ┌────────────────────┐
   │  GET  orders │ ────►  │ systemd timer 5 хв │  ────►   │ crm.lead.add       │
   │  JSON / XML  │        │ realting-sync sync │          │ crm.duplicate.*    │
   └──────────────┘        │ SQLite: стан       │          │ crm.timeline.*     │
                           └────────────────────┘          └────────────────────┘
```

## Стан справ

Сторінка `https://realting.com/ru/account/orders/api-export` закрита авторизацією
(перевірено: `302 → /ru/user/login`), публічної специфікації немає. Тому сервіс написаний
так, щоб невідомий формат не блокував роботу:

- приймає і JSON, і XML;
- розгортає будь-яку обгортку відповіді (`data` / `items` / `orders` / `result` / `rows` …);
- кожне поле шукає за списком вірогідних назв, включно з вкладеними (`client.phone`);
- назви параметрів запиту (дати, пагінація) і спосіб авторизації задаються в конфізі,
  а не в коді;
- команда `probe` показує сиру відповідь і результат мапінгу — цим і знімається специфікація.

Що треба з кабінету: **URL експорту**, **спосіб авторизації** (Bearer / заголовок / параметр),
**приклад відповіді** і **назви параметрів фільтра дат**. Після цього налаштування зводиться
до правки `/etc/realting-sync.env` (і за потреби — файлу мапінгу), без змін у коді.

## Встановлення

```bash
sudo apt update && sudo apt -y install git        # Python 3.10+ в Ubuntu 22.04 вже є
sudo git clone https://github.com/SalesAndManagement/integration-n8n /opt/src
cd /opt/src/integrations/realting-to-bitrix24
sudo git checkout claude/adoring-cori-cmo1iz
sudo ./deploy/install.sh
```

Інсталятор створює системного користувача `realting`, кладе код у `/opt/realting-sync`,
конфіг у `/etc/realting-sync.env` (права `640`), стан у `/var/lib/realting-sync`,
команду `/usr/local/bin/realting-sync` і юніти systemd.

## Налаштування і запуск

```bash
sudo nano /etc/realting-sync.env                  # URL, токен, вебхук Bitrix24
sudo -u realting realting-sync check              # конфіг + доступ до обох систем
sudo -u realting realting-sync probe              # формат відповіді Realting і мапінг
sudo -u realting realting-sync sync --dry-run     # прогін без запису в CRM
sudo -u realting realting-sync sync               # бойовий запуск вручну
sudo systemctl enable --now realting-sync.timer   # автозапуск кожні 5 хвилин
```

Перед першим запуском у Bitrix24 має бути створене поле ліда `UF_CRM_REALTING_ID` —
див. [`docs/bitrix24-setup.md`](docs/bitrix24-setup.md). `check` окремо про це попередить.

## Команди

| Команда | Що робить |
|---------|-----------|
| `realting-sync sync` | забирає нові заявки і створює ліди |
| `realting-sync sync --dry-run` | показує, що створив би, нічого не пишучи |
| `realting-sync sync --since 2026-09-01` | разова довибірка за період (ігнорує збережений стан) |
| `realting-sync sync --force` | ігнорує локальну базу оброблених (дедуп у Bitrix24 лишається) |
| `realting-sync probe --days 30 --raw` | сира відповідь Realting + результат мапінгу |
| `realting-sync check` | перевірка конфігу, доступу до Realting і Bitrix24 |
| `realting-sync stats` | скільки заявок синхронізовано і до якого моменту |

Логи: `journalctl -u realting-sync -f`, статус таймера: `systemctl list-timers realting-sync*`.

## Як це працює

**Вікно вибірки.** Від часу останнього успішного прогону мінус нахлест 15 хвилин
(`SYNC_OVERLAP_MINUTES`) до «зараз». Перший запуск бере останні 7 днів
(`SYNC_FIRST_RUN_DAYS`). Вікно зсувається **лише після повністю успішного прогону**:
якщо хоч одна заявка впала на помилці Bitrix24, наступний запуск спробує її ще раз.

**Дедуплікація, три рівні:**

1. SQLite `/var/lib/realting-sync/state.db` — заявка, вже імпортована цим сервісом, не піде вдруге;
2. `crm.lead.list` за `UF_CRM_REALTING_ID` — захист після відновлення з бекапу чи `--force`;
3. `crm.duplicate.findbycomm` за телефоном (потім за поштою) — якщо контакт уже є в CRM,
   новий лід не створюється, а в таймлайн наявного падає коментар із текстом заявки.
   Вимикається через `BITRIX_COMMENT_ON_DUPLICATE=false`.

**Стійкість.** Мережеві збої та 5xx — 3 спроби з експоненційним бекофом. `QUERY_LIMIT_EXCEEDED`
від Bitrix24 — окремий ретрай із паузою; крім того, запити до порталу тротляться до
~2 на секунду (ліміт Bitrix24). Заявки без ID або без жодного контакту не імпортуються,
а потрапляють у лог із причиною.

**Мапінг.** Типові назви полів — у `src/realting_sync/normalize.py`. Якщо реальні назви інші,
не треба правити код: створіть `/etc/realting-sync.map.json` і вкажіть його в
`REALTING_FIELD_MAP_FILE`:

```json
{
  "external_id": ["zayavka_nomer"],
  "phone": ["kontakt.telefon"],
  "comment": ["tekst_soobscheniya"]
}
```

Повна таблиця полів — у [`docs/field-mapping.md`](docs/field-mapping.md).

## Розробка

```bash
cd integrations/realting-to-bitrix24
PYTHONPATH=src:tests python3 -m unittest discover -s tests -t tests -v
```

90 тестів. Юніт-тести не ходять у мережу (транспорт і клієнти підмінені заглушками);
наскрізний `test_e2e.py` піднімає локальний HTTP-сервер і ганяє через нього справжній CLI.
Покрито нормалізацію (JSON, XML, вкладені обʼєкти, сміттєві телефони), пагінацію,
авторизацію в усіх режимах, ретраї, троттлінг, дедуплікацію, `--dry-run`, поведінку
при частковому збої та розбір конфігу.

## Структура

```
src/realting_sync/
  config.py       конфіг зі змінних оточення + валідація
  httpclient.py   urllib + ретраї, розбір JSON/XML
  realting.py     клієнт експорту: авторизація, параметри, пагінація
  normalize.py    сира заявка → Lead (мапінг, чистка телефонів/пошти)
  bitrix.py       REST-клієнт Bitrix24: троттлінг, помилки, поля ліда
  state.py        SQLite: оброблені заявки, момент останньої синхронізації
  sync.py         оркестрація: вікно, дедуп, підсумковий звіт
  cli.py          sync | probe | check | stats
deploy/           install.sh, systemd service + timer
docs/             мапінг полів, налаштування Bitrix24
```
