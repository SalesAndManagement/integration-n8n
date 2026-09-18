#!/usr/bin/env bash
# Створює .env із .env.example: генерує всі секрети, питає домен і пошту.
# Використання:
#   ./scripts/init-env.sh                          # інтерактивно, з підказкою домену
#   ./scripts/init-env.sh auto me@mail.com         # домен підібрати автоматично
#   ./scripts/init-env.sh n8n.mydomain.com me@mail.com
set -euo pipefail

cd "$(dirname "$0")/.."

DOMAIN="${1:-}"
EMAIL="${2:-}"

# Чи є вже ініціалізовані томи? Якщо так, секрети МІНЯТИ НЕ МОЖНА:
# ключ шифрування зашитий у /home/node/.n8n/config, паролі — у томі postgres.
STACK_EXISTS=0
if docker volume ls -q --filter label=com.docker.compose.project=n8n 2>/dev/null | grep -q .; then
  STACK_EXISTS=1
fi

KEEP_SECRETS=0
if [ -f .env ]; then
  echo "Файл .env вже існує."
  if [ "$STACK_EXISTS" -eq 1 ]; then
    cat <<'WARN'

⚠ Стек уже запускався: томи postgres/n8n створені.
   Перегенерація секретів ЗЛАМАЄ його — n8n не зможе ані розшифрувати
   свої дані (mismatching encryption key), ані зайти в базу (password
   authentication failed).

   1) залишити секрети, змінити лише домен і пошту   ← безпечно
   2) перегенерувати все (знадобиться очистити дані: ./scripts/reset-data.sh)

WARN
    read -r -p "Ваш вибір [1/2, Enter = 1]: " choice
    case "${choice:-1}" in
      1) KEEP_SECRETS=1 ;;
      2) KEEP_SECRETS=0
         echo "Секрети буде перегенеровано. Після цього обов'язково: ./scripts/reset-data.sh" ;;
      *) echo "Незрозуміла відповідь — скасовано."; exit 1 ;;
    esac
  else
    read -r -p "Перезаписати? [yes/NO] " a
    [ "$a" = "yes" ] || { echo "Скасовано."; exit 1; }
  fi
  cp .env ".env.bak.$(date +%Y%m%d-%H%M%S)"
fi

# "auto" — підібрати домен автоматично (хостнейм провайдера або sslip.io)
if [ "$DOMAIN" = "auto" ] || [ "$DOMAIN" = "--auto" ]; then
  DOMAIN="$(./scripts/detect-domain.sh --quiet)" || {
    echo "Автопідбір домену не вдався — вкажіть домен вручну."; exit 1; }
  echo "Автоматично підібрано домен: $DOMAIN"
fi

if [ -z "$DOMAIN" ]; then
  SUGGESTED="$(./scripts/detect-domain.sh --quiet 2>/dev/null || true)"
  if [ -n "$SUGGESTED" ]; then
    echo "Доступний безкоштовний домен для цього сервера: $SUGGESTED"
    read -r -p "Домен для n8n [Enter = $SUGGESTED]: " DOMAIN
    DOMAIN="${DOMAIN:-$SUGGESTED}"
  fi
fi

while [ -z "$DOMAIN" ]; do
  read -r -p "Домен для n8n (напр. n8n.mydomain.com): " DOMAIN
done
while [ -z "$EMAIL" ]; do
  read -r -p "Пошта для Let's Encrypt: " EMAIL
done

# зчитати наявні секрети ДО перезапису файлу
if [ "$KEEP_SECRETS" -eq 1 ]; then
  OLD_PG_PW="$(grep -m1 '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)"
  OLD_PG_APP_PW="$(grep -m1 '^POSTGRES_NON_ROOT_PASSWORD=' .env | cut -d= -f2-)"
  OLD_KEY="$(grep -m1 '^N8N_ENCRYPTION_KEY=' .env | cut -d= -f2-)"
fi

cp .env.example .env

if [ "$KEEP_SECRETS" -eq 1 ]; then
  POSTGRES_PASSWORD="$OLD_PG_PW"
  POSTGRES_NON_ROOT_PASSWORD="$OLD_PG_APP_PW"
  N8N_ENCRYPTION_KEY="$OLD_KEY"
else
  # hex, а не base64: жодних символів, які треба екранувати у .env
  POSTGRES_PASSWORD="$(openssl rand -hex 24)"
  POSTGRES_NON_ROOT_PASSWORD="$(openssl rand -hex 24)"
  N8N_ENCRYPTION_KEY="$(openssl rand -hex 32)"
fi

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
  Паролі БД .............. $([ "$KEEP_SECRETS" -eq 1 ] && echo "збережено наявні" || echo "згенеровано")
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
