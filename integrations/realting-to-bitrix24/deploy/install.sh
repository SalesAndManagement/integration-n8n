#!/usr/bin/env bash
# Встановлення сервісу на Ubuntu/Debian. Запускати з кореня репозиторію папки:
#   sudo ./deploy/install.sh
set -euo pipefail

SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR=/opt/realting-sync
STATE_DIR=/var/lib/realting-sync
ENV_FILE=/etc/realting-sync.env

if [[ $EUID -ne 0 ]]; then
  echo "Запускайте через sudo" >&2
  exit 1
fi

python3 - <<'PY'
import sys
if sys.version_info < (3, 10):
    sys.exit(f"Потрібен Python 3.10+, знайдено {sys.version.split()[0]}")
print(f"Python {sys.version.split()[0]} — ok")
PY

echo "==> Користувач realting"
id -u realting &>/dev/null || useradd --system --home "$STATE_DIR" --shell /usr/sbin/nologin realting

echo "==> Код у $APP_DIR"
mkdir -p "$APP_DIR"
rm -rf "$APP_DIR/src"
cp -r "$SRC_DIR/src" "$APP_DIR/src"
chown -R root:root "$APP_DIR"

echo "==> Каталог стану $STATE_DIR"
mkdir -p "$STATE_DIR"
chown realting:realting "$STATE_DIR"
chmod 750 "$STATE_DIR"

echo "==> Конфіг $ENV_FILE"
if [[ ! -f "$ENV_FILE" ]]; then
  cp "$SRC_DIR/.env.example" "$ENV_FILE"
  echo "    створено з шаблону — заповніть його перед запуском"
fi
chown root:realting "$ENV_FILE"
chmod 640 "$ENV_FILE"

echo "==> Команда realting-sync"
cat > /usr/local/bin/realting-sync <<'WRAP'
#!/usr/bin/env bash
export PYTHONPATH=/opt/realting-sync/src
exec python3 -m realting_sync "$@"
WRAP
chmod 755 /usr/local/bin/realting-sync

echo "==> systemd"
install -m 644 "$SRC_DIR/deploy/realting-sync.service" /etc/systemd/system/realting-sync.service
install -m 644 "$SRC_DIR/deploy/realting-sync.timer" /etc/systemd/system/realting-sync.timer
systemctl daemon-reload

cat <<'NEXT'

Готово. Далі:
  1. sudo nano /etc/realting-sync.env          # URL, токен, вебхук Bitrix24
  2. sudo -u realting realting-sync check      # перевірка доступів
  3. sudo -u realting realting-sync probe      # подивитись формат відповіді Realting
  4. sudo -u realting realting-sync sync --dry-run
  5. sudo systemctl enable --now realting-sync.timer
  6. journalctl -u realting-sync -f            # логи
NEXT
