# Claude Agent SDK — Telegram-агент на своєму сервері

Самохостний сервіс: Telegram-бот, усередині якого крутиться агентний цикл Claude Code
через [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk) (Python).
Агент має вбудовані інструменти (`Read`, `Glob`, `Grep`, `WebSearch`, `WebFetch`) і власний
інструмент `trigger_workflow`, який запускає n8n workflow через webhook.

Оплата — **кредити Anthropic API** (platform.claude.com → Billing). Логін від claude.ai
для сторонніх продуктів на Agent SDK не використовується — тільки `ANTHROPIC_API_KEY`.

## Що всередині

```
app/config.py   налаштування зі змінних оточення + валідація
app/tools.py    власний інструмент trigger_workflow, відданий через in-process MCP-сервер SDK
app/agent.py    обгортка над query(): сесія на чат, політика дозволів, ліміти
app/bot.py      Telegram: whitelist, команди, індикатор роботи, розбиття довгих відповідей
app/main.py     точка входу (long polling)
```

Потік одного повідомлення:

```
Telegram → whitelist → ClaudeAgent.ask(chat_id, prompt)
        → query(prompt, options) — агентний цикл SDK
        → інструменти: Read/Glob/Grep/WebSearch/WebFetch/mcp__n8n__trigger_workflow
        → ResultMessage (текст, вартість, кількість кроків) → відповідь у чат
```

## Швидкий старт

1. **Ключ API** — platform.claude.com → Settings → API Keys → Create Key.
   Поповнення: там же, Billing → prepaid credits. Ліміт витрат — `/settings/limits`.
2. **Токен бота** — @BotFather → `/newbot`.
3. **Свій Telegram id** — @userinfobot.

### Локально

```bash
cd claude-agent-bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # заповни ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USER_IDS
set -a && source .env && set +a
python -m app.main
```

SDK не читає `.env` сам — саме тому змінні експортуються в оточення процесу.

### Docker (так і має жити на сервері)

```bash
cd claude-agent-bot
cp .env.example .env     # відредагуй
docker compose up -d --build
docker compose logs -f claude-agent-bot
```

Разом з n8n у тій самій мережі:

```bash
docker compose --profile n8n up -d --build
```

Тоді в `.env` став `N8N_WEBHOOK_BASE_URL=http://n8n:5678` — контейнери бачать одне одного
за іменем сервісу, а сам n8n назовні слухає тільки `127.0.0.1:5678`
(за потреби заведи його через reverse proxy з TLS).

## Змінні оточення

| Змінна | За замовчуванням | Призначення |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Обов'язково. Ключ з Console, з нього списуються кредити |
| `TELEGRAM_BOT_TOKEN` | — | Обов'язково. Токен від @BotFather |
| `TELEGRAM_ALLOWED_USER_IDS` | — | Обов'язково. Хто має доступ, через кому. Без цього бот не стартує |
| `CLAUDE_MODEL` | `claude-opus-5` | `claude-sonnet-5` або `claude-haiku-4-5` — дешевше |
| `CLAUDE_EFFORT` | `medium` | Глибина міркувань: `low`…`max`. Прямо впливає на витрати |
| `MAX_BUDGET_USD` | `0.50` | Стоп по оцінці вартості одного запиту |
| `MAX_TURNS` | `20` | Стоп по кількості кроків агента |
| `AGENT_WORKSPACE` | `/data/workspace` | Каталог, який агент бачить як робочий |
| `ALLOWED_TOOLS` | див. `.env.example` | Білий список інструментів |
| `SYSTEM_PROMPT` | укр. асистент | Системний промпт агента |
| `SHOW_TOOL_TRACE` | `1` | Дописувати під відповіддю інструменти й вартість |
| `N8N_WEBHOOK_BASE_URL` | порожньо | База n8n. Порожня — `trigger_workflow` повертає помилку |
| `N8N_WEBHOOK_TOKEN` | порожньо | Значення заголовка `Authorization` для Header Auth у n8n |
| `LOG_LEVEL` | `INFO` | `DEBUG` покаже stderr CLI-процесу агента |

## Команди бота

- звичайний текст — задача агенту
- `/reset` — забути контекст цього чату
- `/status` — модель, ліміти, дозволені інструменти, поточна сесія
- `/help` — довідка

## Як це працює з n8n

У n8n зроби workflow з нодою **Webhook** (метод POST, шлях, наприклад, `new-lead`).
Далі в Telegram:

> запусти workflow new-lead з даними: email a@b.c, джерело — телеграм

Агент викличе `mcp__n8n__trigger_workflow` з `webhook_path="webhook/new-lead"` і
JSON-тілом, отримає відповідь n8n і перекаже її. Тестовий URL ноди — `webhook-test/...`,
продакшн — `webhook/...`.

Інструмент навмисно приймає **тільки відносний шлях**: повний URL, схема або `..`
відхиляються, щоб промпт не міг відправити запит на чужий хост.

## Модель безпеки

Агент виконує інструменти на твоєму сервері, тому:

- **Whitelist обов'язковий.** Порожній `TELEGRAM_ALLOWED_USER_IDS` — бот не стартує.
- **Білий список інструментів.** Усе, чого немає в `ALLOWED_TOOLS`, отримує явну відмову
  через колбек `can_use_tool` — у headless-режимі нема кому натискати «дозволити».
- `Bash`, `Write`, `Edit` за замовчуванням вимкнені. Вмикай свідомо — це доступ на запис
  і виконання команд у контейнері.
- **Пісочниця.** `cwd` агента — `AGENT_WORKSPACE`, контейнер працює від non-root
  користувача. Не монтуй туди чутливі каталоги; репозиторій зручно давати `:ro`.
- **`setting_sources=[]`** — SDK не підтягує `~/.claude` і `.claude/` проєкту, конфіг бота
  задається тільки змінними оточення.
- Секрети — у `.env` (він у `.gitignore`), не у промпті й не в коді.

## Гроші

`MAX_BUDGET_USD` зупиняє один запит по клієнтській оцінці вартості, `MAX_TURNS` — по
кількості кроків. Фактична вартість кожної відповіді пишеться під нею (`💲`) і в логи.
Жорсткий стоп на рівні акаунта — ліміт витрат у Console; auto-reload краще тримати
вимкненим, поки не зрозумієш реальне споживання.

Дешевше: `CLAUDE_MODEL=claude-sonnet-5` або `claude-haiku-4-5`, `CLAUDE_EFFORT=low`.

## Додати свій інструмент

У `app/tools.py`:

```python
@tool("create_invoice", "Виставити рахунок клієнту", {"client": str, "amount": float})
async def create_invoice(args: dict[str, Any]) -> dict[str, Any]:
    ...
    return {"content": [{"type": "text", "text": "Рахунок №123 створено"}]}
```

Додай функцію у список `tools=[...]` у `create_sdk_mcp_server`, а ім'я
`mcp__n8n__create_invoice` — в `ALLOWED_TOOLS`. Схема інструмента описується простим
маппінгом типів; повертати треба `{"content": [...]}`, а на помилку — ще й `"is_error": True`.

## Діагностика

| Симптом | Причина |
|---|---|
| `Не задано ANTHROPIC_API_KEY` | Змінні не експортовані в оточення процесу (SDK не читає `.env`) |
| `Invalid API key` / `Not logged in` | Ключ невірний або скінчились кредити в Console |
| Агент пише, що інструмент вимкнено | Його немає в `ALLOWED_TOOLS` — додай свідомо |
| `n8n відповів 404` | Workflow неактивний або шлях тестовий/продакшн переплутано |
| Бот мовчить на повідомлення | Твого id немає в `TELEGRAM_ALLOWED_USER_IDS` — дивись логи |
| Порожня відповідь | Досягнуто `MAX_BUDGET_USD` або `MAX_TURNS` — бот скаже це прямо |

Детальні логи: `LOG_LEVEL=DEBUG`.
