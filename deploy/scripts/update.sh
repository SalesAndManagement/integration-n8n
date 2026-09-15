#!/usr/bin/env bash
# Оновлення n8n до свіжого образу з попереднім бекапом.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> Бекап перед оновленням"
./scripts/backup.sh

echo "==> Тягну нові образи"
docker compose pull

echo "==> Перезапуск"
docker compose up -d

echo "==> Прибирання старих образів"
docker image prune -f

docker compose ps
