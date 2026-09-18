#!/usr/bin/env bash
# Встановлення без root і без Docker: усе лягає в цю папку та в ~/.cache.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
VENDOR="$ROOT/vendor"
PLAYWRIGHT_MCP_VERSION="${PLAYWRIGHT_MCP_VERSION:-0.0.81}"
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$HOME/.cache/ms-playwright}"
SKIP_BROWSER="${SKIP_BROWSER:-0}"

say()  { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
warn() { printf '\033[33m!  %s\033[0m\n' "$1"; }
die()  { printf '\033[31mПомилка: %s\033[0m\n' "$1" >&2; exit 1; }

# --- Python ---------------------------------------------------------------
say "Python"
command -v python3 >/dev/null || die "python3 не знайдено"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
    || die "потрібен Python 3.10+, знайдено $(python3 --version)"
python3 --version

if [ ! -d .venv ]; then
    python3 -m venv .venv 2>/dev/null \
        || die "не вдалося створити venv. Без root спробуй: python3 -m pip install --user virtualenv && python3 -m virtualenv .venv"
fi
./.venv/bin/pip install -q --upgrade pip
./.venv/bin/pip install -q -r requirements.txt
echo "залежності Python встановлено"

# --- Node + браузер -------------------------------------------------------
BROWSER_OK=0
CLI=""

if [ "$SKIP_BROWSER" = "1" ]; then
    say "Браузер пропущено (SKIP_BROWSER=1)"
elif ! command -v node >/dev/null; then
    warn "node не знайдено — браузер буде вимкнено."
    warn "Поставити без root: curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash && nvm install 22"
elif [ "$(node -p 'process.versions.node.split(".")[0]')" -lt 18 ]; then
    warn "потрібен node 18+, знайдено $(node --version) — браузер буде вимкнено"
else
    say "Playwright MCP"
    node --version
    mkdir -p "$VENDOR"
    npm install --silent --prefix "$VENDOR" "@playwright/mcp@$PLAYWRIGHT_MCP_VERSION"
    CLI="$VENDOR/node_modules/@playwright/mcp/cli.js"
    [ -f "$CLI" ] || die "cli.js не знайдено після встановлення: $CLI"

    PW_CLI="$VENDOR/node_modules/playwright-core/cli.js"
    [ -f "$PW_CLI" ] || PW_CLI="$VENDOR/node_modules/@playwright/mcp/node_modules/playwright-core/cli.js"
    [ -f "$PW_CLI" ] || die "playwright-core не знайдено"

    say "Chromium (~115 МБ у $PLAYWRIGHT_BROWSERS_PATH)"
    node "$PW_CLI" install chromium

    # Головна перевірка безрутового встановлення: системні бібліотеки chromium
    # ставляться через apt, а це вже потребує root. Перевіряємо, чого бракує.
    BIN="$(find "$PLAYWRIGHT_BROWSERS_PATH" -type f -name 'headless_shell' 2>/dev/null | head -1)"
    [ -n "$BIN" ] || BIN="$(find "$PLAYWRIGHT_BROWSERS_PATH" -type f -name 'chrome' 2>/dev/null | head -1)"
    if [ -z "$BIN" ]; then
        warn "не знайшов бінарник chromium — браузер буде вимкнено"
    else
        MISSING="$(ldd "$BIN" 2>/dev/null | awk '/not found/{print $1}' | sort -u || true)"
        if [ -n "$MISSING" ]; then
            warn "chromium не запуститься, бракує системних бібліотек:"
            printf '   %s\n' $MISSING
            warn "їх ставить root: sudo npx playwright install-deps chromium"
            warn "Поки що вмикаю BROWSER_ENABLED=0 — WebSearch і WebFetch працюють без браузера."
        else
            BROWSER_OK=1
            echo "усі бібліотеки на місці"
        fi
    fi
fi

# --- .env -----------------------------------------------------------------
say ".env"
[ -f .env ] || cp .env.example .env

BROWSER_OK="$BROWSER_OK" CLI="$CLI" ROOT="$ROOT" python3 - <<'PY'
import os
from pathlib import Path

root = os.environ["ROOT"]
updates = {
    "AGENT_WORKSPACE": f"{root}/data/workspace",
    "STATE_FILE": f"{root}/data/sessions.json",
    "BROWSER_PROFILE_DIR": f"{root}/data/browser-profile",
    "BROWSER_ENABLED": os.environ["BROWSER_OK"],
}
cli = os.environ.get("CLI")
if cli:
    updates["BROWSER_MCP_CLI"] = cli

path = Path(".env")
lines = path.read_text(encoding="utf-8").splitlines()
seen = set()
out = []
for line in lines:
    key = line.split("=", 1)[0].strip()
    if key in updates:
        out.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        out.append(line)
for key, value in updates.items():
    if key not in seen:
        out.append(f"{key}={value}")
path.write_text("\n".join(out) + "\n", encoding="utf-8")
print("шляхи в .env проставлено")
PY

mkdir -p data/workspace data/claude data/browser-profile

# --- підсумок -------------------------------------------------------------
say "Готово"
echo "Папка агента: $ROOT/data"
if [ "$BROWSER_OK" = "1" ]; then
    echo "Браузер: увімкнено"
else
    echo "Браузер: вимкнено (лишились WebSearch і WebFetch)"
fi
cat <<EOF

Далі:
  1) впиши ключі:  nano .env
     ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USER_IDS
  2) запусти:      ./scripts/run-native.sh
  3) у фоні:       nohup ./scripts/run-native.sh > data/bot.log 2>&1 &
EOF
