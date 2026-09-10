#!/usr/bin/env bash
# Deep self-heal: перевіряє HTTP-здоров'я, черги, диск. Викликається systemd-таймером кожні 2 хв.
set -uo pipefail
STACK_DIR="${STACK_DIR:-/opt/n8n}"
STATE=/var/lib/n8n-watchdog
mkdir -p "$STATE"
cd "$STACK_DIR" || exit 0

log() { logger -t n8n-watchdog "$*"; echo "$(date -Is) $*"; }

# 1. диск: якщо >90% — чистимо докер і старі бекапи
USE=$(df --output=pcent "$STACK_DIR" | tail -1 | tr -dc '0-9')
if [[ "${USE:-0}" -ge 90 ]]; then
  log "disk ${USE}% — pruning"
  docker system prune -af --filter "until=72h" >/dev/null 2>&1
  find "$STACK_DIR/backups" -name 'n8n-*.sql.gz' -mtime +7 -delete
fi

# 2. контейнери, що впали в exited — піднімаємо
if docker compose ps --status exited --status dead -q | grep -q .; then
  log "found stopped containers — compose up -d"
  docker compose up -d --remove-orphans
fi

# 3. HTTP healthcheck головного інстансу
FAILS_FILE="$STATE/fails"
FAILS=$(cat "$FAILS_FILE" 2>/dev/null || echo 0)
if docker compose exec -T n8n wget -q --spider http://127.0.0.1:5678/healthz 2>/dev/null; then
  echo 0 > "$FAILS_FILE"
else
  FAILS=$((FAILS+1)); echo "$FAILS" > "$FAILS_FILE"
  log "healthz failed ($FAILS)"
  if [[ "$FAILS" -ge 3 ]]; then
    log "restarting n8n stack"
    docker compose restart n8n n8n-worker n8n-webhook
    echo 0 > "$FAILS_FILE"
  fi
fi

# 4. воркери живі? (у queue-режимі без воркерів нічого не виконується)
RUNNING=$(docker compose ps --status running -q n8n-worker | wc -l)
if [[ "$RUNNING" -eq 0 ]]; then
  log "no workers running — starting"
  docker compose up -d n8n-worker
fi
