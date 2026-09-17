#!/usr/bin/env bash
# Preflight-перевірка перед першим запуском:
# чи вказує домен на цей сервер і чи вільні/доступні порти 80 та 443.
# Запуск із каталогу deploy/:  ./scripts/check-dns.sh
set -uo pipefail

cd "$(dirname "$0")/.."
[ -f .env ] || { echo "Немає .env — спершу: cp .env.example .env"; exit 1; }
set -a; . ./.env; set +a

ok()   { printf '  \033[1;32m✔\033[0m %s\n' "$*"; }
warn() { printf '  \033[1;33m!\033[0m %s\n' "$*"; }
bad()  { printf '  \033[1;31m✘\033[0m %s\n' "$*"; FAILED=1; }
FAILED=0

echo
echo "Домен: ${N8N_DOMAIN:-<не задано>}"

case "${N8N_DOMAIN:-}" in
  ""|n8n.example.com)
    bad "N8N_DOMAIN не заповнено у .env"; exit 1 ;;
esac

# --- 1. Публічний IP сервера ---
SERVER_IP="$(curl -fsS --max-time 10 https://api.ipify.org || curl -fsS --max-time 10 https://ifconfig.me || true)"
[ -n "$SERVER_IP" ] && ok "IP сервера: $SERVER_IP" || warn "Не вдалося визначити зовнішній IP сервера"

# --- 2. Куди резолвиться домен ---
resolve() {
  if command -v dig >/dev/null 2>&1; then
    dig +short A "$1" | grep -E '^[0-9.]+$'
  else
    getent ahostsv4 "$1" | awk '{print $1}' | sort -u
  fi
}
DOMAIN_IPS="$(resolve "$N8N_DOMAIN")"

if [ -z "$DOMAIN_IPS" ]; then
  bad "A-запис для $N8N_DOMAIN не знайдено. Створіть його в панелі DNS і зачекайте до 30 хв."
else
  ok "A-запис: $(echo "$DOMAIN_IPS" | tr '\n' ' ')"
  if [ -n "$SERVER_IP" ]; then
    if echo "$DOMAIN_IPS" | grep -qx "$SERVER_IP"; then
      ok "Домен вказує на цей сервер"
    elif echo "$DOMAIN_IPS" | grep -qE '^(104\.(1[6-9]|2[0-9]|3[01])\.|172\.6[4-9]\.|172\.7[01]\.|188\.114\.|190\.93\.|197\.234\.|198\.41\.|162\.15[89]\.|173\.245\.|103\.2[12][0-9]\.)'; then
      warn "Домен вказує на Cloudflare (проксі увімкнено, помаранчева хмарка)."
      warn "Для першого випуску сертифіката вимкніть проксі (DNS only), потім за бажанням увімкніть назад із SSL mode = Full (strict)."
    else
      bad "Домен вказує на інший IP, ніж цей сервер ($SERVER_IP). Виправте A-запис."
    fi
  fi
fi

# --- 3. Порти ---
echo
for p in 80 443; do
  if ss -ltnp 2>/dev/null | grep -q ":$p "; then
    HOLDER="$(ss -ltnp 2>/dev/null | grep ":$p " | head -1)"
    if echo "$HOLDER" | grep -q 'docker\|caddy'; then
      ok "Порт $p зайнятий нашим же caddy — це нормально при повторній перевірці"
    else
      bad "Порт $p зайнятий стороннім процесом: $HOLDER"
    fi
  else
    ok "Порт $p вільний"
  fi
done

if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  for p in 80 443; do
    ufw status 2>/dev/null | grep -q "^$p" && ok "ufw пропускає $p" || bad "ufw блокує $p — виконайте: ufw allow $p/tcp"
  done
fi

# --- 4. Пошта для Let's Encrypt ---
echo
case "${LETSENCRYPT_EMAIL:-}" in
  ""|admin@example.com) warn "LETSENCRYPT_EMAIL не заповнено — сповіщення про сертифікати не надходитимуть" ;;
  *) ok "LETSENCRYPT_EMAIL: $LETSENCRYPT_EMAIL" ;;
esac

echo
if [ "$FAILED" -eq 0 ]; then
  echo "Все готово. Запускайте: docker compose up -d"
else
  echo "Є проблеми вище — виправте їх, інакше Let's Encrypt не видасть сертифікат."
  exit 1
fi
