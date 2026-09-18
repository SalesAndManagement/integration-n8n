# Claude Agent SDK — Telegram-агент на своєму сервері

Самохостний сервіс: Telegram-бот, усередині якого крутиться агентний цикл Claude Code
через [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk) (Python).

Агент уміє:

- **працювати з файлами й терміналом** — `Read`, `Write`, `Edit`, `Bash`, `Glob`, `Grep`
  у межах своєї папки;
- **ходити в інтернет** — `WebSearch` і `WebFetch` для швидкого пошуку та читання сторінок,
  плюс **справжній браузер** через [Playwright MCP](https://github.com/microsoft/playwright-mcp):
  33 інструменти — відкрити сторінку, клікнути, заповнити форму, залогінитись, зробити
  скріншот або PDF;
- **запускати n8n workflow** власним інструментом `trigger_workflow`.

Оплата — **кредити Anthropic API** (platform.claude.com → Billing). Логін від claude.ai для
сторонніх продуктів на Agent SDK не використовується, тільки `ANTHROPIC_API_KEY`.

## Де він живе: папка агента

Агенту виділяється одна папка на хості — вона монтується в контейнер як `/data` і є всім
його світом. Поклади проєкт куди зручно, наприклад `/opt/claude-agent`:

```
/opt/claude-agent/                 # сюди кладеш цю папку (git clone або scp)
├── .env                           # ключі й налаштування (не в git)
├── docker-compose.yml
├── Dockerfile
├── app/
└── data/                          # ←→ /data у контейнері, створиться на першому старті
    ├── workspace/                 # cwd агента: тут він читає, пише, виконує команди
    │   └── .playwright-mcp/       # снапшоти сторінок і скріншоти з браузера
    ├── browser-profile/           # профіль Chromium: логіни й куки живуть між рестартами
    ├── claude/                    # сесії Claude Code (щоб працював resume)
    └── sessions.json              # мапа chat_id → session_id
```

`data/` — звичайна папка на хості, не прихований docker-том: її видно через `ls`, можна
бекапити `tar`, можна покласти туди файли, і агент їх одразу побачить. Хочеш дати йому
робочий репозиторій — розкоментуй у `docker-compose.yml` монтування:

```yaml
- ../workflows:/data/workspace/workflows:ro   # :ro = тільки читання
```

За межі `/data` агент не бачить нічого: ані файлової системи хоста, ані інших контейнерів
(крім n8n, якщо він у тій самій мережі). Хочеш дати ще один каталог — монтуй його явно.

## Режими роботи

`AGENT_MODE` у `.env`:

| Режим | Поведінка |
|---|---|
| `sandbox` *(типово)* | Усі інструменти Claude Code без підтверджень. Межа — сам контейнер: усередині `/data` агент робить що завгодно, назовні не дістає |
| `restricted` | Працюють тільки інструменти зі списку `ALLOWED_TOOLS`, решта отримує явну відмову. `Bash`/`Write`/`Edit` вимкнені |

`sandbox` — те, заради чого це все: агент сам ставить пакети, пише скрипти, запускає їх,
дивиться на помилку й переписує. Ціна в тому, що в межах `/data` він може й зіпсувати —
тому там не має бути нічого, чого не шкода, а `data/` варто періодично бекапити.
Якщо потрібен суворіший контроль — `restricted`.

## Швидкий старт

1. **Ключ API** — platform.claude.com → Settings → API Keys → Create Key.
   Поповнення: там же, Billing → prepaid credits. Ліміт витрат — `/settings/limits`.
2. **Токен бота** — @BotFather → `/newbot`.
3. **Свій Telegram id** — @userinfobot.

```bash
cd /opt/claude-agent            # там, де лежить docker-compose.yml
cp .env.example .env            # заповни ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USER_IDS
docker compose up -d --build
docker compose logs -f claude-agent-bot
```

Перша збірка довга (~10 хв): тягнеться Node, Python, Playwright і Chromium; образ виходить
приблизно на 1.5 ГБ. Разом із n8n у тій самій мережі:

```bash
docker compose --profile n8n up -d --build
```

Тоді в `.env` став `N8N_WEBHOOK_BASE_URL=http://n8n:5678` — контейнери бачать одне одного за
іменем сервісу, а сам n8n назовні слухає лише `127.0.0.1:5678`.

### Без root і без Docker

Якщо на сервері немає sudo або `docker ps` каже `permission denied` — усе ставиться в
домашню папку:

```bash
./scripts/setup-native.sh     # venv + залежності + Playwright MCP + chromium
nano .env                     # ключі
./scripts/run-native.sh       # запуск
```

Скрипт нічого не чіпає поза цією папкою та `~/.cache` і сам обходить те, що без root
не ставиться:

| Перешкода | Що робить скрипт |
|---|---|
| `python3 -m venv` падає з `ensurepip is not available` (немає пакета `python3-venv`) | Ставить `uv` у `~/.local/bin` і створює venv ним. Якщо й системний Python не годиться — `uv` завантажує власний Python 3.12 |
| Немає Node | Качає офіційний тарбол у `vendor/node`, без `nvm` і без змін у `~/.bashrc` |
| Chromium не запуститься без системних бібліотек (вони з apt, тобто з root) | Викликає `scripts/install-browser-libs.sh`: качає потрібні `.deb` і **розпаковує їх у `vendor/syslibs`**, не встановлюючи в систему. Якщо не вийшло — називає конкретні `.so` і ставить `BROWSER_ENABLED=0` |

### Бібліотеки chromium без root

`apt-get download` і `dpkg -x` працюють від звичайного користувача — пакет можна завантажити
й розпакувати, не встановлюючи. `scripts/install-browser-libs.sh` робить саме це:

1. `ldd` показує, яких `.so` бракує;
2. по таблиці «бібліотека → пакет» збирає список разом із залежностями, пропускаючи те,
   що в системі вже стоїть, і ніколи не чіпаючи `libc6` та компанію — підміна glibc ламає все;
3. качає й розпаковує в `vendor/syslibs`, потім перевіряє `ldd` знову (кілька кіл, бо пакет
   може привести за собою нові залежності);
4. пробує запустити `chromium --version` і лише після цього ставить `BROWSER_ENABLED=1`
   та `BROWSER_LD_LIBRARY_PATH`.

Індекс apt скрипт тримає свій, у `vendor/apt`: системний без root не оновити, а якщо він
застарілий — завантаження дає 404 на версію, якої в дзеркалі вже немає.

Запустити окремо, коли бібліотеки додались пізніше:

```bash
./scripts/install-browser-libs.sh
```

Якщо не спрацювало — агент лишається робочим без браузера (`WebSearch`, `WebFetch`), а з root
це одна команда: `sudo npx playwright install-deps chromium`, далі `BROWSER_ENABLED=1`.

Тримати процес живим без systemd:

```bash
# найпростіше
nohup ./scripts/run-native.sh > data/bot.log 2>&1 &

# автостарт після перезавантаження, без root
crontab -e
@reboot cd ~/integration-n8n/claude-agent-bot && ./scripts/run-native.sh >> data/bot.log 2>&1
```

Якщо на сервері дозволені user-юніти systemd — надійніше через них:
`systemctl --user enable --now claude-agent-bot` (юніт треба створити самому,
`ExecStart=%h/integration-n8n/claude-agent-bot/scripts/run-native.sh`).

`.env` сервіс читає сам (`app/env_file.py`) — `source .env` не потрібен і не рекомендується:
на значеннях із пробілами, як `SYSTEM_PROMPT`, він ламається.

## Браузер

Працює через Playwright MCP, інструменти видно як `mcp__playwright__browser_*`. Приклади
задач, які агент виконує сам:

> зайди в кабінет постачальника, вивантаж прайс і поклади у workspace

> відкрий https://…/admin, знайди помилку 500 у логах на сторінці й покажи скріншот

Агент бачить сторінку як **accessibility-снапшот** (структурований текст), а не як картинку,
тому клікає за елементами надійно й не палить токени на скріншоти. Снапшоти й скріншоти
лягають у `data/workspace/.playwright-mcp/` — звідти агент читає їх звичайним `Read`, і ти
теж можеш туди зазирнути.

`BROWSER_PERSIST_PROFILE=1` означає, що профіль Chromium зберігається в
`data/browser-profile/`: залогінився один раз — сесія жива після рестарту. Поводься з цією
папкою як із секретом, там лежать куки. Потрібна навпаки чистота — постав `0`, і кожен
запуск буде з нуля.

## Змінні оточення

| Змінна | За замовчуванням | Призначення |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Обов'язково. Ключ з Console, з нього списуються кредити |
| `TELEGRAM_BOT_TOKEN` | — | Обов'язково. Токен від @BotFather |
| `TELEGRAM_ALLOWED_USER_IDS` | — | Числові id, через кому. Дізнатись у @userinfobot |
| `TELEGRAM_ALLOWED_USERNAMES` | — | `@username` через кому, як альтернатива id. Хоча б один зі списків має бути непорожній |
| `AGENT_MODE` | `sandbox` | `sandbox` або `restricted` (див. вище) |
| `ALLOWED_TOOLS` | див. `.env.example` | Білий список інструментів для `restricted` |
| `CLAUDE_MODEL` | `claude-opus-5` | `claude-sonnet-5` або `claude-haiku-4-5` — дешевше |
| `CLAUDE_EFFORT` | `medium` | Глибина міркувань: `low`…`max`. Прямо впливає на витрати |
| `MAX_BUDGET_USD` | `0.50` | Стоп по оцінці вартості одного запиту |
| `MAX_TURNS` | `20` | Стоп по кількості кроків агента |
| `AGENT_WORKSPACE` | `/data/workspace` | Робоча папка агента |
| `SYSTEM_PROMPT` | укр. асистент | Системний промпт |
| `SHOW_TOOL_TRACE` | `1` | Дописувати під відповіддю інструменти й вартість |
| `BROWSER_ENABLED` | `1` | Вимкни, якщо браузер не потрібен — мінус процеси й пам'ять |
| `BROWSER_HEADLESS` | `1` | Без вікна. На сервері інакше й не буде |
| `BROWSER_NO_SANDBOX` | `1` | Пісочниця chromium не піднімається під non-root у контейнері |
| `BROWSER_PERSIST_PROFILE` | `1` | Зберігати логіни між рестартами |
| `BROWSER_VIEWPORT` | `1280x720` | Розмір вікна |
| `BROWSER_CAPS` | `vision,pdf` | Додаткові можливості: `vision`, `pdf`, `devtools` |
| `BROWSER_MCP_COMMAND` | `npx` | У Docker перекрито на `playwright-mcp` (пакет уже в образі) |
| `BROWSER_MCP_CLI` | порожньо | Шлях до `cli.js` при встановленні без root; проставляє `setup-native.sh` |
| `BROWSER_NODE` | `node` | Який `node` запускати. `setup-native.sh` ставить сюди `vendor/node/bin/node`, якщо качав його сам |
| `BROWSER_LD_LIBRARY_PATH` | порожньо | Де лежать бібліотеки chromium, розпаковані без root; проставляє `install-browser-libs.sh` |
| `N8N_WEBHOOK_BASE_URL` | порожньо | База n8n. Порожня — `trigger_workflow` поверне помилку |
| `N8N_WEBHOOK_TOKEN` | порожньо | Значення заголовка `Authorization` для Header Auth у n8n |
| `LOG_LEVEL` | `INFO` | `DEBUG` покаже stderr CLI-процесу агента |

## Кому відкрити доступ

```bash
TELEGRAM_ALLOWED_USER_IDS=123456789,987654321
TELEGRAM_ALLOWED_USERNAMES=@PetrDoroshSM,@SM_Vladyslav_Integrator
```

Працює об'єднання: пускаємо того, чий id у першому списку **або** чий username у другому.
`@` і регістр не мають значення. Кожен співрозмовник отримує власну сесію (свій контекст
і свій `/reset`), але робоча папка й гаманець спільні: усі витрати йдуть з одного
API-ключа, а `MAX_BUDGET_USD` обмежує кожен запит окремо, не суму за день.

## Команди бота

- звичайний текст — задача агенту
- `/reset` — забути контекст цього чату
- `/status` — режим, модель, ліміти, браузер, поточна сесія
- `/help` — довідка

## Як це працює з n8n

У n8n зроби workflow з нодою **Webhook** (метод POST, шлях, наприклад, `new-lead`).
Далі в Telegram:

> запусти workflow new-lead з даними: email a@b.c, джерело — телеграм

Агент викличе `mcp__n8n__trigger_workflow` з `webhook_path="webhook/new-lead"` і JSON-тілом,
отримає відповідь n8n і перекаже її. Тестовий URL ноди — `webhook-test/...`,
продакшн — `webhook/...`.

Інструмент навмисно приймає **тільки відносний шлях**: повний URL, схема або `..`
відхиляються, щоб промпт не міг відправити запит на чужий хост.

## Межі й ризики

- **Whitelist обов'язковий.** Обидва списки порожні — бот не стартує.
  Хто в списку, той керує агентом; поводься з цим як із SSH-доступом до контейнера.
- **id надійніше за @username.** Числовий id незмінний, username — ні: людина може його
  звільнити, і хтось інший займе те саме імʼя разом із доступом. Username зручний для
  старту, коли id ще невідомий: щойно людина напише боту, її id зʼявиться в логах
  (`Доступ за @… · id=…`) — перенеси його в `TELEGRAM_ALLOWED_USER_IDS`.
  Telegram не дає боту перетворити @username на id, поки людина сама не написала,
  тому іншого шляху дізнатись id немає.
- **Контейнер — єдина межа.** У `sandbox` агент виконує довільні команди всередині нього.
  Не монтуй туди чутливі каталоги хоста, не клади в `.env` зайвих секретів, не давай
  контейнеру доступу до docker-сокета.
- **Non-root.** Процес працює під користувачем `agent` (uid 10001).
- **`setting_sources=[]`** — SDK не підтягує `~/.claude` і `.claude/` проєкту; конфіг агента
  задається тільки змінними оточення.
- **Промпт-ін'єкції реальні.** Агент читає сторінки й файли; текст звідти може містити
  інструкції. Тому межа папки й відсутність секретів усередині важливіші за будь-які
  формулювання в системному промпті.
- **Що бекапити:** `data/` цілком, окремо `.env`. Це весь стан сервісу.

## Гроші

`MAX_BUDGET_USD` зупиняє один запит по клієнтській оцінці вартості, `MAX_TURNS` — по
кількості кроків. Фактична вартість кожної відповіді пишеться під нею (`💲`) і в логи.
Жорсткий стоп на рівні акаунта — ліміт витрат у Console; auto-reload краще тримати
вимкненим, поки не зрозумієш реальне споживання.

Дешевше: `CLAUDE_MODEL=claude-sonnet-5` або `claude-haiku-4-5`, `CLAUDE_EFFORT=low`.
Робота з браузером дорожча за звичайний чат: кожен снапшот сторінки — це токени.

## Додати свій інструмент

У `app/tools.py`:

```python
@tool("create_invoice", "Виставити рахунок клієнту", {"client": str, "amount": float})
async def create_invoice(args: dict[str, Any]) -> dict[str, Any]:
    ...
    return {"content": [{"type": "text", "text": "Рахунок №123 створено"}]}
```

Додай функцію у список `tools=[...]` у `create_sdk_mcp_server`. У `restricted` ще й додай ім'я
`mcp__n8n__create_invoice` в `ALLOWED_TOOLS`; у `sandbox` воно доступне одразу. Повертати
треба `{"content": [...]}`, а на помилку — ще й `"is_error": True`.

Зовнішній MCP-сервер (свій або чужий) підключається так само, як браузер у `app/browser.py`:
конфіг `{"type": "stdio", "command": ..., "args": [...]}` у `mcp_servers`.

## Тести

```bash
pip install -r requirements-dev.txt
python -m pytest
```

25 перевірок без мережі й без звернень до API: валідація конфігу, обидва режими дозволів,
аргументи браузера, persistence сесій, інструмент n8n проти локального вебхука, розбиття
повідомлень. Прогоняй після кожної правки `app/` — саме тут ловляться помилки в політиці
дозволів.

## Діагностика

| Симптом | Причина |
|---|---|
| `Не задано ANTHROPIC_API_KEY` | Змінні не експортовані в оточення процесу (SDK не читає `.env`) |
| `Invalid API key` / `Not logged in` | Ключ невірний або скінчились кредити в Console |
| `Chromium distribution 'chrome' is not found` | Загубився прапорець `--browser chromium`; MCP пішов шукати системний Google Chrome |
| `error while loading shared libraries: lib…so` | Немає системних бібліотек chromium → `./scripts/install-browser-libs.sh` (без root) або з root `sudo npx playwright install-deps chromium` |
| `apt-get download` дає 404 | Застарілий індекс. Скрипт тримає свій у `vendor/apt`; якщо не допомогло — `rm -rf vendor/apt` і запустити ще раз |
| `permission denied … docker.sock` | Юзер не в групі `docker`. Без root — став через `scripts/setup-native.sh` |
| Браузер падає без пояснень | Мало `/dev/shm` — у compose має бути `shm_size: "1gb"` |
| Агент пише, що інструмент вимкнено | Режим `restricted`, інструмента немає в `ALLOWED_TOOLS` |
| `n8n відповів 404` | Workflow неактивний або переплутано тестовий/продакшн шлях |
| Бот мовчить на повідомлення | Твого id немає в `TELEGRAM_ALLOWED_USER_IDS` — дивись логи |
| Порожня відповідь | Досягнуто `MAX_BUDGET_USD` або `MAX_TURNS` — бот скаже це прямо |

Детальні логи: `LOG_LEVEL=DEBUG`.
