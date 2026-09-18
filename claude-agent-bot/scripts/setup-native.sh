#!/usr/bin/env bash
# Встановлення без root і без Docker: усе лягає в цю папку та в ~/.cache.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
VENDOR="$ROOT/vendor"
NODE_DIR="$VENDOR/node"
PLAYWRIGHT_MCP_VERSION="${PLAYWRIGHT_MCP_VERSION:-0.0.81}"
NODE_CHANNEL="${NODE_CHANNEL:-latest-v22.x}"
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$HOME/.cache/ms-playwright}"
SKIP_BROWSER="${SKIP_BROWSER:-0}"

say()  { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
warn() { printf '\033[33m!  %s\033[0m\n' "$1"; }
die()  { printf '\033[31mПомилка: %s\033[0m\n' "$1" >&2; exit 1; }

UV_BIN=""

# --- Python ---------------------------------------------------------------
say "Python"
command -v python3 >/dev/null || die "python3 не знайдено"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
    || die "потрібен Python 3.10+, знайдено $(python3 --version)"
python3 --version

find_uv() {
    if command -v uv >/dev/null; then command -v uv; return; fi
    [ -x "$HOME/.local/bin/uv" ] && echo "$HOME/.local/bin/uv"
}

create_venv() {
    if [ -x .venv/bin/python ]; then
        echo "venv уже є"
        [ -x .venv/bin/pip ] || UV_BIN="$(find_uv)"
        return 0
    fi

    # 1) Штатний шлях. На Debian падає, якщо не поставлено python3-venv (а це root).
    if python3 -m venv .venv >/dev/null 2>&1 && [ -x .venv/bin/pip ]; then
        echo "venv створено модулем venv"
        return 0
    fi
    rm -rf .venv
    warn "python3 -m venv недоступний (немає ensurepip: пакет python3-venv ставиться з root)"

    # 2) uv створює venv сам, без ensurepip і без прав root.
    UV_BIN="$(find_uv)"
    if [ -z "$UV_BIN" ]; then
        say "Ставлю uv у ~/.local/bin (без root)"
        curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1 || true
        UV_BIN="$(find_uv)"
    fi
    [ -n "$UV_BIN" ] || return 1

    if "$UV_BIN" venv .venv >/dev/null 2>&1 && [ -x .venv/bin/python ]; then
        echo "venv створено через uv ($("$UV_BIN" --version))"
        return 0
    fi

    # 3) Системний python не годиться навіть для uv — беремо окремий інтерпретатор.
    warn "системний python не підійшов, uv поставить власний"
    rm -rf .venv
    "$UV_BIN" python install 3.12 >/dev/null 2>&1 || return 1
    "$UV_BIN" venv --python 3.12 .venv >/dev/null 2>&1 || return 1
    [ -x .venv/bin/python ] || return 1
    echo "venv створено через uv на власному Python 3.12"
}

create_venv || die "не вдалося створити venv ні модулем venv, ні через uv. Покажи вивід — розберемось"

if [ -n "$UV_BIN" ]; then
    "$UV_BIN" pip install --python .venv/bin/python -q -r requirements.txt
else
    ./.venv/bin/pip install -q --upgrade pip
    ./.venv/bin/pip install -q -r requirements.txt
fi
echo "залежності Python встановлено"

# --- Node -----------------------------------------------------------------
NODE_BIN=""
NPM_BIN=""

node_arch() {
    case "$(uname -m)" in
        x86_64|amd64) echo x64 ;;
        aarch64|arm64) echo arm64 ;;
        *) echo "" ;;
    esac
}

if [ "$SKIP_BROWSER" = "1" ]; then
    say "Браузер пропущено (SKIP_BROWSER=1)"
elif command -v node >/dev/null && [ "$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)" -ge 18 ]; then
    NODE_BIN="$(command -v node)"
elif [ -x "$NODE_DIR/bin/node" ]; then
    NODE_BIN="$NODE_DIR/bin/node"
else
    ARCH="$(node_arch)"
    if [ -z "$ARCH" ]; then
        warn "невідома архітектура $(uname -m) — браузер буде вимкнено"
    else
        say "Node не знайдено — качаю офіційний тарбол у $NODE_DIR (без root)"
        FILE="$(curl -fsSL "https://nodejs.org/dist/$NODE_CHANNEL/SHASUMS256.txt" \
            | awk -v suffix="linux-$ARCH.tar.gz" '$2 ~ suffix"$" {print $2}' | head -1 || true)"
        if [ -z "$FILE" ]; then
            warn "не вдалося дізнатись версію Node — браузер буде вимкнено"
        else
            mkdir -p "$NODE_DIR"
            curl -fsSL "https://nodejs.org/dist/$NODE_CHANNEL/$FILE" | tar xz -C "$NODE_DIR" --strip-components=1
            NODE_BIN="$NODE_DIR/bin/node"
            echo "встановлено $("$NODE_BIN" --version)"
        fi
    fi
fi

# --- Playwright MCP + chromium --------------------------------------------
BROWSER_OK=0
CLI=""

if [ -n "$NODE_BIN" ]; then
    NPM_BIN="$(dirname "$NODE_BIN")/npm"
    [ -x "$NPM_BIN" ] || NPM_BIN="$(command -v npm || true)"
    [ -n "$NPM_BIN" ] || warn "npm не знайдено поруч із node"
fi

if [ -n "$NODE_BIN" ] && [ -n "$NPM_BIN" ]; then
    say "Playwright MCP"
    "$NODE_BIN" --version
    mkdir -p "$VENDOR"
    PATH="$(dirname "$NODE_BIN"):$PATH" "$NPM_BIN" install --silent --prefix "$VENDOR" "@playwright/mcp@$PLAYWRIGHT_MCP_VERSION"

    CLI="$VENDOR/node_modules/@playwright/mcp/cli.js"
    [ -f "$CLI" ] || die "cli.js не знайдено після встановлення: $CLI"

    PW_CLI="$VENDOR/node_modules/playwright-core/cli.js"
    [ -f "$PW_CLI" ] || PW_CLI="$VENDOR/node_modules/@playwright/mcp/node_modules/playwright-core/cli.js"
    [ -f "$PW_CLI" ] || die "playwright-core не знайдено"

    say "Chromium (~115 МБ у $PLAYWRIGHT_BROWSERS_PATH)"
    "$NODE_BIN" "$PW_CLI" install chromium

    # Головна перевірка безрутового встановлення: системні бібліотеки chromium
    # ставляться через apt, а це вже потребує root. Дивимось, чого бракує.
    BIN="$(find "$PLAYWRIGHT_BROWSERS_PATH" -type f -name 'headless_shell' 2>/dev/null | head -1)"
    [ -n "$BIN" ] || BIN="$(find "$PLAYWRIGHT_BROWSERS_PATH" -type f -name 'chrome' 2>/dev/null | head -1)"
    if [ -z "$BIN" ]; then
        warn "не знайшов бінарник chromium — браузер буде вимкнено"
    else
        MISSING="$(ldd "$BIN" 2>/dev/null | awk '/not found/{print $1}' | sort -u || true)"
        if [ -n "$MISSING" ]; then
            warn "chromium не запуститься, бракує системних бібліотек:"
            printf '   %s\n' $MISSING
            warn "їх ставить тільки root: sudo npx playwright install-deps chromium"
            warn "Поки що BROWSER_ENABLED=0 — WebSearch і WebFetch працюють без браузера."
        else
            BROWSER_OK=1
            echo "усі бібліотеки на місці"
        fi
    fi
fi

# --- .env -----------------------------------------------------------------
say ".env"
[ -f .env ] || cp .env.example .env

BROWSER_OK="$BROWSER_OK" CLI="$CLI" NODE_BIN="$NODE_BIN" ROOT="$ROOT" python3 - <<'PY'
import os
from pathlib import Path

root = os.environ["ROOT"]
updates = {
    "AGENT_WORKSPACE": f"{root}/data/workspace",
    "STATE_FILE": f"{root}/data/sessions.json",
    "BROWSER_PROFILE_DIR": f"{root}/data/browser-profile",
    "BROWSER_ENABLED": os.environ["BROWSER_OK"],
}
if os.environ.get("CLI"):
    updates["BROWSER_MCP_CLI"] = os.environ["CLI"]
if os.environ.get("NODE_BIN"):
    updates["BROWSER_NODE"] = os.environ["NODE_BIN"]

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
echo "Python:       $(./.venv/bin/python --version)"
if [ "$BROWSER_OK" = "1" ]; then
    echo "Браузер:      увімкнено ($("$NODE_BIN" --version))"
else
    echo "Браузер:      вимкнено (лишились WebSearch і WebFetch)"
fi
cat <<EOF

Далі:
  1) впиши ключі:  nano .env
     ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USER_IDS
  2) запусти:      ./scripts/run-native.sh
  3) у фоні:       nohup ./scripts/run-native.sh > data/bot.log 2>&1 &
EOF
