#!/usr/bin/env bash
# Підбирає робочий домен для сервера без покупки й реєстрації.
#
# Порядок пошуку:
#   1. Зворотний DNS (PTR) цього IP — у Contabo це готовий хостнейм
#      виду vmi1234567.contaboserver.net, він уже резолвиться.
#   2. sslip.io — wildcard-сервіс: 203-0-113-45.sslip.io завжди
#      резолвиться у 203.0.113.45, реєструвати нічого не треба.
#
# Використання:
#   ./scripts/detect-domain.sh            # з поясненнями
#   ./scripts/detect-domain.sh --quiet    # лише домен, для скриптів
set -uo pipefail

QUIET=0
[ "${1:-}" = "--quiet" ] && QUIET=1
say() { [ "$QUIET" -eq 1 ] || printf '%s\n' "$*" >&2; }

# forward-резолв: список IPv4 для імені
resolve4() {
  if command -v dig >/dev/null 2>&1; then
    dig +short A "$1" 2>/dev/null | grep -E '^[0-9.]+$'
  else
    getent ahostsv4 "$1" 2>/dev/null | awk '{print $1}' | sort -u
  fi
}

# reverse-резолв: ім'я для IP
resolve_ptr() {
  if command -v dig >/dev/null 2>&1; then
    dig +short -x "$1" 2>/dev/null | head -1 | sed 's/\.$//'
  else
    getent hosts "$1" 2>/dev/null | awk '{print $2}' | head -1
  fi
}

IP="$(curl -fsS --max-time 10 https://api.ipify.org || curl -fsS --max-time 10 https://ifconfig.me)"
if [ -z "$IP" ]; then
  say "Не вдалося визначити зовнішній IP сервера."
  exit 1
fi
say "IP сервера: $IP"

# --- Кандидат 1: хостнейм від провайдера (PTR) ---
PTR="$(resolve_ptr "$IP")"
if [ -n "$PTR" ] && [ "$PTR" != "$IP" ]; then
  if resolve4 "$PTR" | grep -qx "$IP"; then
    say "Знайдено хостнейм провайдера: $PTR (резолвиться на цей сервер)"
    printf '%s\n' "$PTR"
    exit 0
  fi
  say "PTR $PTR не резолвиться назад на $IP — пропускаю."
fi
say "Готового хостнейма у зворотному DNS немає."

# --- Кандидат 2: sslip.io ---
SSLIP="${IP//./-}.sslip.io"
if resolve4 "$SSLIP" | grep -qx "$IP"; then
  say "Використовую wildcard-домен: $SSLIP"
  printf '%s\n' "$SSLIP"
  exit 0
fi

say "sslip.io недоступний (можливо, DNS-резолвер сервера його блокує)."
say "Зареєструйте безкоштовний домен на https://www.duckdns.org і вкажіть IP $IP."
exit 1
