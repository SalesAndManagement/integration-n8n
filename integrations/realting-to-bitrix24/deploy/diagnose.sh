#!/usr/bin/env bash
# Чому заявка не дійшла: один прогін по всьому ланцюгу.
#   sudo ./deploy/diagnose.sh [годин_назад]
set -uo pipefail

HOURS="${1:-6}"
SINCE="${HOURS} hours ago"
CADDY_LOG=/var/log/caddy/realting-webhook.log

if [[ $EUID -ne 0 ]]; then
  echo "Запускайте через sudo — інакше не видно журналів" >&2
  exit 1
fi

line() { printf '\n=== %s\n' "$1"; }

line "1. Служби"
for unit in caddy realting-webhook realting-sync.timer; do
  printf '%-22s %s\n' "$unit" "$(systemctl is-active "$unit" 2>/dev/null)"
done

line "2. Чи стукав хтось ззовні (журнал Caddy, за $HOURS год)"
if [[ -f "$CADDY_LOG" ]]; then
  python3 - "$CADDY_LOG" "$HOURS" <<'PY'
import json, sys, time
from collections import Counter

path, hours = sys.argv[1], float(sys.argv[2])
cutoff = time.time() - hours * 3600
statuses, rows = Counter(), []
for raw in open(path, encoding="utf-8", errors="replace"):
    try:
        entry = json.loads(raw)
    except ValueError:
        continue
    if entry.get("ts", 0) < cutoff:
        continue
    request = entry.get("request", {})
    status = entry.get("status")
    statuses[f'{request.get("method","?")} {request.get("uri","?").split("?")[0]} → {status}'] += 1
    rows.append((entry["ts"], request.get("method"), request.get("uri", ""),
                 status, request.get("headers", {}).get("User-Agent", ["—"])[0],
                 request.get("remote_ip")))

if not rows:
    print("  Жодного запиту ззовні. Realting до сервера не достукався:")
    print("  адреса в кабінеті не збережена, збережена інша, або їхній тест нічого не слав.")
else:
    for key, count in statuses.most_common():
        print(f"  {count:>4}  {key}")
    print("\n  Останні запити:")
    for ts, method, uri, status, agent, ip in rows[-10:]:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
        safe = uri.split("token=")[0] + ("token=***" if "token=" in uri else "")
        print(f"  {stamp}  {method} {safe} → {status}  {ip}  {agent}")
PY
else
  echo "  Файл $CADDY_LOG не знайдено — Caddy налаштований без логу або ще нічого не писав"
fi

line "3. Журнал приймача (за $HOURS год)"
journalctl -u realting-webhook --since "$SINCE" --no-pager -o cat | tail -30 || true

line "4. Черга і стан"
sudo -u realting realting-sync stats

line "5. Другий канал: що бачить експорт просто зараз"
sudo -u realting realting-sync sync --dry-run

line "6. Журнал планових прогонів (за $HOURS год)"
journalctl -u realting-sync --since "$SINCE" --no-pager -o cat | tail -20 || true

line "7. Доступність ззовні"
DOMAIN="$(grep -o '^[^ ]*' /etc/caddy/Caddyfile 2>/dev/null | head -1)"
if [[ -n "${DOMAIN:-}" && "$DOMAIN" != "{\$REALTING_WEBHOOK_DOMAIN}" ]]; then
  echo -n "  https://$DOMAIN/healthz → "; curl -sS -m 10 "https://$DOMAIN/healthz"; echo
else
  DOMAIN="$(systemctl show caddy -p Environment --value | tr ' ' '\n' | grep REALTING_WEBHOOK_DOMAIN= | cut -d= -f2)"
  [[ -n "$DOMAIN" ]] && { echo -n "  https://$DOMAIN/healthz → "; curl -sS -m 10 "https://$DOMAIN/healthz"; echo; } \
                     || echo "  домен не визначено"
fi

cat <<'HINT'

--- Як читати ---
Розділ 2 порожній        → Realting не слав нічого: перевірте, що адреса збережена в кабінеті.
У розділі 2 статус 401   → слав, але без нашого токена. Дивіться розділ 3: там видно, чим саме
                           він автентифікується, і треба перемкнути WEBHOOK_AUTH_MODE.
У розділі 2 статус 404   → слав на інший шлях. Порівняйте з WEBHOOK_PATH.
У розділі 2 статус 200   → заявка прийнята; дивіться розділ 4 (черга) і 3 (чи не «замаскована»).
Розділ 5 показує «створено 0, пропущено N» → нові заявки в експорті є, але контакти закриті.
HINT
