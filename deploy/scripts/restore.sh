#!/usr/bin/env bash
# Відновлення бази з дампа, створеного backup.sh.
# Використання:  ./scripts/restore.sh backups/n8n-db-20260915-120000.dump
set -euo pipefail

DUMP="${1:?Вкажіть шлях до .dump файлу}"
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a

[ -f "$DUMP" ] || { echo "Файл не знайдено: $DUMP"; exit 1; }

read -r -p "Перезаписати базу ${POSTGRES_DB} з ${DUMP}? Дані буде втрачено. [yes/NO] " ans
[ "$ans" = "yes" ] || { echo "Скасовано."; exit 1; }

echo "==> Зупиняю n8n"
docker compose stop n8n n8n-worker 2>/dev/null || true

echo "==> Відновлення"
docker compose exec -T postgres \
  pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --role="$POSTGRES_NON_ROOT_USER" \
  < "$DUMP"

echo "==> Запускаю n8n"
docker compose up -d
echo "Готово. Переконайтесь, що N8N_ENCRYPTION_KEY у .env той самий, що й на момент бекапу."
