#!/usr/bin/env bash
# n8n production installer for a fresh Debian/Ubuntu VPS (Contabo & co), run as root.
#   bash install.sh n8n.example.com you@example.com
set -euo pipefail

DOMAIN="${1:-}"
ACME_EMAIL="${2:-}"
STACK_DIR="${STACK_DIR:-/opt/n8n}"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

[[ $EUID -eq 0 ]] || { echo "run as root"; exit 1; }
[[ -n "$DOMAIN" ]] || { echo "usage: bash install.sh <domain> <email>"; exit 1; }
[[ -n "$ACME_EMAIL" ]] || { echo "usage: bash install.sh <domain> <email>"; exit 1; }

log() { echo -e "\n\033[1;32m==> $*\033[0m"; }

# ---------- 1. base system ----------
log "system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get upgrade -y
apt-get install -y ca-certificates curl gnupg git jq ufw fail2ban chrony \
  htop ncdu unattended-upgrades apt-listchanges
systemctl enable --now chrony 2>/dev/null || systemctl enable --now chronyd 2>/dev/null || true
timedatectl set-timezone "${TZ:-Europe/Kyiv}" || true

# ---------- 2. swap ----------
RAM_MB=$(free -m | awk '/^Mem:/{print $2}')
SWAP_MB=$(free -m | awk '/^Swap:/{print $2}')
if [[ "$SWAP_MB" -lt 2048 ]]; then
  SWAP_GB=$(( RAM_MB > 16384 ? 8 : 4 ))
  log "creating ${SWAP_GB}G swap"
  fallocate -l "${SWAP_GB}G" /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=$((SWAP_GB*1024))
  chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# ---------- 3. kernel / limits ----------
log "kernel and ulimit tuning"
cat > /etc/sysctl.d/99-n8n.conf <<'EOF'
vm.max_map_count=262144
vm.overcommit_memory=1
vm.swappiness=10
fs.file-max=2097152
fs.inotify.max_user_instances=8192
fs.inotify.max_user_watches=1048576
net.core.somaxconn=65535
net.core.netdev_max_backlog=65535
net.ipv4.tcp_max_syn_backlog=65535
net.ipv4.tcp_fin_timeout=15
net.ipv4.tcp_tw_reuse=1
net.ipv4.ip_local_port_range=1024 65535
EOF
sysctl --system >/dev/null
cat > /etc/security/limits.d/99-n8n.conf <<'EOF'
*  soft  nofile  1048576
*  hard  nofile  1048576
root soft nofile 1048576
root hard nofile 1048576
EOF
echo never > /sys/kernel/mm/transparent_hugepage/enabled 2>/dev/null || true

# ---------- 4. docker ----------
if ! command -v docker >/dev/null 2>&1; then
  log "installing docker"
  curl -fsSL https://get.docker.com | sh
fi
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "50m", "max-file": "5" },
  "live-restore": true,
  "default-ulimits": {
    "nofile": { "Name": "nofile", "Soft": 65535, "Hard": 65535 }
  }
}
EOF
systemctl enable --now docker
systemctl restart docker

# ---------- 5. firewall ----------
log "firewall"
ufw --force reset >/dev/null
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw --force enable
systemctl enable --now fail2ban

# ---------- 6. stack files ----------
log "installing stack into $STACK_DIR"
mkdir -p "$STACK_DIR"/{files,backups}
for f in docker-compose.yml Caddyfile .env.example watchdog.sh; do
  [[ "$SRC_DIR/$f" -ef "$STACK_DIR/$f" ]] || cp "$SRC_DIR/$f" "$STACK_DIR/$f"
done
chmod +x "$STACK_DIR/watchdog.sh"

if [[ ! -f "$STACK_DIR/.env" ]]; then
  log "generating .env with fresh secrets"
  PG_PASS=$(openssl rand -hex 24)
  REDIS_PASS=$(openssl rand -hex 24)
  ENC_KEY=$(openssl rand -hex 32)
  # size postgres/redis to available RAM
  PG_SHARED=$(( RAM_MB / 4 ))MB
  PG_CACHE=$(( RAM_MB * 3 / 4 ))MB
  REDIS_MAX=$(( RAM_MB / 8 ))mb
  NODE_HEAP=$(( RAM_MB / 4 ))
  CPUS=$(nproc)
  WORKERS=$(( CPUS / 2 )); (( WORKERS < 2 )) && WORKERS=2; (( WORKERS > 6 )) && WORKERS=6

  sed -e "s|^DOMAIN=.*|DOMAIN=${DOMAIN}|" \
      -e "s|^ACME_EMAIL=.*|ACME_EMAIL=${ACME_EMAIL}|" \
      -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${PG_PASS}|" \
      -e "s|^REDIS_PASSWORD=.*|REDIS_PASSWORD=${REDIS_PASS}|" \
      -e "s|^N8N_ENCRYPTION_KEY=.*|N8N_ENCRYPTION_KEY=${ENC_KEY}|" \
      -e "s|^N8N_HOST=.*|N8N_HOST=${DOMAIN}|" \
      -e "s|^N8N_EDITOR_BASE_URL=.*|N8N_EDITOR_BASE_URL=https://${DOMAIN}|" \
      -e "s|^WEBHOOK_URL=.*|WEBHOOK_URL=https://${DOMAIN}/|" \
      -e "s|^PG_SHARED_BUFFERS=.*|PG_SHARED_BUFFERS=${PG_SHARED}|" \
      -e "s|^PG_EFFECTIVE_CACHE=.*|PG_EFFECTIVE_CACHE=${PG_CACHE}|" \
      -e "s|^REDIS_MAXMEMORY=.*|REDIS_MAXMEMORY=${REDIS_MAX}|" \
      -e "s|^NODE_OPTIONS=.*|NODE_OPTIONS=--max-old-space-size=${NODE_HEAP}|" \
      -e "s|^WORKER_REPLICAS=.*|WORKER_REPLICAS=${WORKERS}|" \
      "$STACK_DIR/.env.example" > "$STACK_DIR/.env"
  chmod 600 "$STACK_DIR/.env"
fi

# n8n runs as uid 1000 inside the container
chown -R 1000:1000 "$STACK_DIR/files"

# ---------- 7. systemd unit (boot + auto-restart of whole stack) ----------
log "systemd unit"
cat > /etc/systemd/system/n8n.service <<EOF
[Unit]
Description=n8n docker stack
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${STACK_DIR}
ExecStart=/usr/bin/docker compose up -d --remove-orphans
ExecStop=/usr/bin/docker compose down
ExecReload=/usr/bin/docker compose up -d --remove-orphans
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
EOF

# ---------- 8. watchdog timer (deep self-heal) ----------
cat > /etc/systemd/system/n8n-watchdog.service <<EOF
[Unit]
Description=n8n watchdog
After=n8n.service

[Service]
Type=oneshot
Environment=STACK_DIR=${STACK_DIR}
Environment=DOMAIN=${DOMAIN}
ExecStart=${STACK_DIR}/watchdog.sh
EOF
cat > /etc/systemd/system/n8n-watchdog.timer <<'EOF'
[Unit]
Description=run n8n watchdog every 2 minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec=2min
AccuracySec=15s

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable n8n.service
systemctl enable --now n8n-watchdog.timer

# ---------- 9. up ----------
log "pulling images and starting"
cd "$STACK_DIR"
docker compose pull
systemctl start n8n.service
docker compose ps

log "DONE"
echo "URL:      https://${DOMAIN}"
echo "Stack:    ${STACK_DIR}"
echo "Secrets:  ${STACK_DIR}/.env  (ЗБЕРЕЖИ N8N_ENCRYPTION_KEY — без нього креденшели не розшифруються!)"
