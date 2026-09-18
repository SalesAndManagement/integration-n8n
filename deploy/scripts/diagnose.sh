#!/usr/bin/env bash
# Збирає все потрібне для розбору проблеми (502, білий екран, крах контейнера).
# Запуск із каталогу deploy/:  ./scripts/diagnose.sh
set -uo pipefail
cd "$(dirname "$0")/.."

hdr() { printf '\n\033[1;36m=== %s ===\033[0m\n' "$*"; }

hdr "Статус контейнерів"
docker compose ps

hdr "Здоров'я"
for c in postgres n8n caddy; do
  id="$(docker compose ps -q "$c" 2>/dev/null)"
  if [ -z "$id" ]; then
    echo "$c: НЕ ЗАПУЩЕНО"
    continue
  fi
  state="$(docker inspect -f '{{.State.Status}}' "$id")"
  health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}—{{end}}' "$id")"
  restarts="$(docker inspect -f '{{.RestartCount}}' "$id")"
  exitcode="$(docker inspect -f '{{.State.ExitCode}}' "$id")"
  echo "$c: state=$state health=$health restarts=$restarts exit=$exitcode"
done

hdr "Чи бачить Caddy n8n зсередини"
if docker compose exec -T caddy wget -q -O- --timeout=5 http://n8n:5678/healthz 2>&1; then
  echo "  ← n8n відповідає, апстрім живий"
else
  echo "  n8n НЕ відповідає на n8n:5678 — саме через це 502"
fi

hdr "Логи n8n (останні 60 рядків)"
docker compose logs --tail=60 --no-log-prefix n8n 2>&1

hdr "Логи postgres (останні 25)"
docker compose logs --tail=25 --no-log-prefix postgres 2>&1

hdr "Логи caddy (останні 25)"
docker compose logs --tail=25 --no-log-prefix caddy 2>&1

hdr "Ресурси"
free -h 2>/dev/null | head -2
df -h / 2>/dev/null | tail -1

hdr "Ймовірна причина"
LOGS="$(docker compose logs --tail=200 --no-log-prefix n8n 2>&1)"
if echo "$LOGS" | grep -qi "password authentication failed"; then
  echo "Postgres не приймає пароль n8n."
  echo "Найчастіше: .env перегенерували ПІСЛЯ того, як база вже ініціалізувалась."
  echo "Синхронізуйте пароль:"
  echo '  source .env && docker compose exec postgres \'
  echo '    psql -U "$POSTGRES_USER" -c "ALTER USER $POSTGRES_NON_ROOT_USER WITH PASSWORD '"'"'$POSTGRES_NON_ROOT_PASSWORD'"'"';"'
  echo '  docker compose restart n8n'
elif echo "$LOGS" | grep -qi "ECONNREFUSED\|getaddrinfo\|ENOTFOUND"; then
  echo "n8n не достукується до postgres. Перевірте, що контейнер postgres healthy (вище)."
elif echo "$LOGS" | grep -qi "Migration\|migrations"; then
  echo "Схоже, тривають міграції БД. На першому старті це 1-3 хвилини — зачекайте й оновіть сторінку."
elif echo "$LOGS" | grep -qi "mismatching encryption key\|cannot be decrypted"; then
  echo "N8N_ENCRYPTION_KEY у .env не збігається з тим, яким шифрувалась база."
  echo "Поверніть попередній ключ у .env і: docker compose up -d"
elif echo "$LOGS" | grep -qi "EACCES\|permission denied"; then
  echo "Проблема з правами на том /home/node/.n8n."
  echo "  docker compose run --rm --no-deps --user root --entrypoint sh n8n -c 'chown -R node:node /home/node/.n8n'"
elif [ -z "$(docker compose ps -q n8n)" ]; then
  echo "Контейнер n8n узагалі не створено — дивіться помилки docker compose up вище."
else
  echo "Явного маркера не знайдено — дивіться логи n8n вище."
fi
echo
