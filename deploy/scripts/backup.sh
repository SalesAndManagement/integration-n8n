#!/usr/bin/env bash
# Бекап: дамп PostgreSQL + каталог даних n8n (.n8n, включно з encryption key).
# Запуск із каталогу deploy/:  ./scripts/backup.sh
set -euo pipefail

cd "$(dirname "$0")/.."
set -a; . ./.env; set +a

BACKUP_DIR="${BACKUP_DIR:-$PWD/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR"

echo "==> Дамп бази ${POSTGRES_DB}"
docker compose exec -T postgres \
  pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom \
  > "$BACKUP_DIR/n8n-db-$STAMP.dump"

echo "==> Архів /home/node/.n8n"
docker compose run --rm --no-deps --entrypoint sh \
  -v "$BACKUP_DIR:/backup" n8n \
  -c "tar czf /backup/n8n-data-$STAMP.tar.gz -C /home/node .n8n" >/dev/null

echo "==> Прибирання старіших за ${RETENTION_DAYS} днів"
find "$BACKUP_DIR" -name 'n8n-*' -type f -mtime "+$RETENTION_DAYS" -delete

ls -lh "$BACKUP_DIR" | tail -n +2
echo "Готово: $BACKUP_DIR"
