#!/usr/bin/env bash
# TLS-проксі перед приймачем хуків.
#   sudo ./deploy/install-caddy.sh n8n.example.com
set -euo pipefail

DOMAIN="${1:-}"
if [[ -z "$DOMAIN" ]]; then
  echo "Вкажіть домен: sudo ./deploy/install-caddy.sh webhook.example.com" >&2
  echo "Без власного домену можна взяти nip.io: webhook.<IP-з-крапками>.nip.io" >&2
  exit 1
fi
if [[ $EUID -ne 0 ]]; then
  echo "Запускайте через sudo" >&2
  exit 1
fi

SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if ! command -v caddy >/dev/null; then
  echo "==> Встановлюю Caddy"
  apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl gnupg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  apt-get update -y
  apt-get install -y caddy
fi

echo "==> Конфіг Caddy для $DOMAIN"
install -m 644 "$SRC_DIR/deploy/Caddyfile" /etc/caddy/Caddyfile
mkdir -p /etc/systemd/system/caddy.service.d
cat > /etc/systemd/system/caddy.service.d/domain.conf <<CONF
[Service]
Environment=REALTING_WEBHOOK_DOMAIN=$DOMAIN
CONF

echo "==> Фаєрвол: 80/443"
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  ufw allow 80/tcp
  ufw allow 443/tcp
fi

systemctl daemon-reload
systemctl enable --now caddy
systemctl restart caddy

cat <<NEXT

Готово. Адреса для кабінету Realting:
  https://$DOMAIN/realting/webhook?token=<WEBHOOK_TOKEN з /etc/realting-sync.env>

Перевірка (має відповісти {"status":"ok"}):
  curl -s https://$DOMAIN/healthz
NEXT
