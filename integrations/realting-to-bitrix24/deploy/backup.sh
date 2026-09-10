#!/usr/bin/env bash
# Щоденний бекап БД n8n. У крон: 0 3 * * * /opt/n8n/deploy/backup.sh
set -euo pipefail
STACK_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${STACK_DIR}/deploy/backups"
mkdir -p "$OUT"
STAMP="$(date +%F_%H%M)"
docker compose --env-file "${STACK_DIR}/.env" -f "${STACK_DIR}/deploy/docker-compose.yml" \
  exec -T postgres pg_dump -U "${POSTGRES_USER:-n8n}" "${POSTGRES_DB:-n8n}" | gzip > "${OUT}/n8n_${STAMP}.sql.gz"
find "$OUT" -name 'n8n_*.sql.gz' -mtime +14 -delete
echo "OK: ${OUT}/n8n_${STAMP}.sql.gz"
