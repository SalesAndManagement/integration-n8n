#!/usr/bin/env bash
# Створює .env із .env.example: генерує всі секрети, питає домен і пошту.
# Використання:
#   ./scripts/init-env.sh                          # інтерактивно
#   ./scripts/init-env.sh n8n.mydomain.com me@mail.com
set -euo pipefail

cd "$(dirname "$0")/.."

DOMAIN="${1:-}"
EMAIL="${2:-}"

if [ -f .env ]; then
  echo "Файл .env вже існує."
  read -r -p "Перезаписати? Поточні секрети буде збережено в .env.bak [yes/NO] " a
  [ "$a" = "yes" ] || { echo "Скасовано."; exit 1; }
  cp .env ".env.bak.$(date +%Y%m%d-%H%M%S)"
fi

while [ -z "$DOMAIN" ]; do
  read -r -p "Домен для n8n (напр. n8n.mydomain.com): " DOMAIN
done
while [ -z "$EMAIL" ]; do
  read -r -p "Пошта для Let's Encrypt: " EMAIL
done

cp .env.example .env

# hex, а не base64: жодних символів, які треба екранувати у .env
POSTGRES_PASSWORD="$(openssl rand -hex 24)"
POSTGRES_NON_ROOT_PASSWORD="$(openssl rand -hex 24)"
N8N_ENCRYPTION_KEY="$(openssl rand -hex 32)"

# розмір пулу з'єднань за кількістю ядер
CORES="$(nproc 2>/dev/null || echo 2)"
POOL=$(( CORES * 2 )); [ "$POOL" -lt 2 ] && POOL=2; [ "$POOL" -gt 16 ] && POOL=16

set_var() {
  local key="$1" val="$2"
  if grep -q "^${key}=" .env; then
    python3 - "$key" "$val" <<'PY'
import sys, io
key, val = sys.argv[1], sys.argv[2]
lines = io.open('.env', encoding='utf-8').read().splitlines(True)
out = []
for line in lines:
    if line.startswith(key + '='):
        out.append(f'{key}={val}\n')
    else:
        out.append(line)
io.open('.env', 'w', encoding='utf-8').writelines(out)
PY
  else
    printf '%s=%s\n' "$key" "$val" >> .env
  fi
}

set_var N8N_DOMAIN                 "$DOMAIN"
set_var LETSENCRYPT_EMAIL          "$EMAIL"
set_var POSTGRES_PASSWORD          "$POSTGRES_PASSWORD"
set_var POSTGRES_NON_ROOT_PASSWORD "$POSTGRES_NON_ROOT_PASSWORD"
set_var N8N_ENCRYPTION_KEY         "$N8N_ENCRYPTION_KEY"
set_var DB_POSTGRESDB_POOL_SIZE    "$POOL"

chmod 600 .env

cat <<MSG

Файл .env створено (права 600, у git не потрапляє).

  Домен .................. $DOMAIN
  Пошта Let's Encrypt .... $EMAIL
  Паролі БД .............. згенеровано
  Пул з'єднань ........... $POOL  (ядер: $CORES)

┌───────────────────────────────────────────────────────────────┐
│  ЗБЕРЕЖІТЬ ЦЕЙ КЛЮЧ У МЕНЕДЖЕР ПАРОЛІВ — ОКРЕМО ВІД СЕРВЕРА:  │
└───────────────────────────────────────────────────────────────┘
N8N_ENCRYPTION_KEY=$N8N_ENCRYPTION_KEY

Ним зашифровані всі credentials у базі. Без нього бекап БД марний,
і після переїзду на інший сервер доступи не розшифруються.

Далі:
  ./scripts/check-dns.sh     # перевірити домен і порти
  docker compose up -d
MSG
