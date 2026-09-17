#!/usr/bin/env bash
# Що слухає на порту публікації BAF: веб-сервер, TLS, чи транспорт 1С.
# Запускати перед check-odata.sh, якщо браузер віддає SSL-помилку або 404.
#
#   ./probe-host.sh vladiyan.extranet.club 46795

set -u
HOST="${1:-vladiyan.extranet.club}"
PORT="${2:-46795}"

echo "=== TCP: чи порт відкритий"
if command -v nc >/dev/null 2>&1; then
  nc -z -w 5 "$HOST" "$PORT" && echo "порт відкритий" || echo "порт закритий або фільтрується"
else
  echo "nc не встановлений — пропускаю"
fi

echo
echo "=== HTTP (без TLS)"
curl -sS -m 15 -D - -o /dev/null "http://${HOST}:${PORT}/" 2>&1 | head -8

echo
echo "=== HTTPS (з TLS)"
curl -k -sS -m 15 -D - -o /dev/null "https://${HOST}:${PORT}/" 2>&1 | head -8

echo
echo "=== TLS handshake"
if command -v openssl >/dev/null 2>&1; then
  echo | openssl s_client -connect "${HOST}:${PORT}" -servername "$HOST" 2>&1 \
    | grep -E 'CONNECTED|subject=|issuer=|Protocol|Cipher|verify|alert|no peer' | head -10
else
  echo "openssl не встановлений — пропускаю"
fi

cat <<'HINT'

--- Як читати ---
Server: Apache / Microsoft-IIS у HTTP-відповіді
    -> це веб-сервер, публікація 1С тут є, далі запускайте check-odata.sh з http://
HTTP скидає з'єднання (Connection reset), а openssl показує сертифікат
    -> на порту ТІЛЬКИ https. Усі перевірки робіть з https і -k, якщо
       сертифікат самопідписаний (verify error num=18)
TCP відкритий, але HTTP-відповіді немає жодної
    -> це не веб-сервер, а транспорт 1С (кластер). Веб-публікації немає,
       OData недоступний, поки хостер не опублікує базу на веб-сервері.

Точну відповідь дає сама 1С: вікно запуску -> база -> "Змінити"
(або Довідка -> Про програму, рядок підключення):
    ws=http://host/base        -> веб-публікація є
    Srvr="host:1541";Ref="..." -> пряме підключення до кластера, публікації немає
HINT
