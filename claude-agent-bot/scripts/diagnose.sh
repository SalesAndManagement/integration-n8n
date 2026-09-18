#!/usr/bin/env bash
# Самоперевірка ланцюга: ключ -> API -> Claude Code -> браузер.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

[ -d .venv ] || { echo "Спершу ./scripts/setup-native.sh" >&2; exit 1; }

if [ -d "$ROOT/vendor/node/bin" ]; then
    export PATH="$ROOT/vendor/node/bin:$PATH"
fi
export CLAUDE_CONFIG_DIR="${CLAUDE_CONFIG_DIR:-$ROOT/data/claude}"
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$HOME/.cache/ms-playwright}"
mkdir -p "$CLAUDE_CONFIG_DIR"

exec ./.venv/bin/python scripts/diagnose.py
