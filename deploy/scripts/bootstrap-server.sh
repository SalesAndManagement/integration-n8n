#!/usr/bin/env bash
# Підготовка чистого Ubuntu 22.04/24.04 (Contabo VPS) під n8n:
# оновлення, Docker + compose, файрвол, swap, автооновлення безпеки.
# Запуск від root:  bash bootstrap-server.sh
set -euo pipefail

log() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "Запустіть від root"; exit 1; }

log "Оновлення системи"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get upgrade -y -qq
apt-get install -y -qq ca-certificates curl gnupg ufw fail2ban unattended-upgrades dnsutils

log "Часовий пояс -> ${TIMEZONE:-Europe/Warsaw}"
timedatectl set-timezone "${TIMEZONE:-Europe/Warsaw}"

if ! command -v docker >/dev/null 2>&1; then
  log "Встановлення Docker Engine + compose plugin"
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
else
  log "Docker уже встановлено: $(docker --version)"
fi

log "Обмеження розміру логів Docker"
cat > /etc/docker/daemon.json <<'JSON'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
JSON
systemctl restart docker

if ! swapon --show | grep -q .; then
  log "Створення swap 2G"
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  sysctl -w vm.swappiness=10 >/dev/null
  grep -q 'vm.swappiness' /etc/sysctl.conf || echo 'vm.swappiness=10' >> /etc/sysctl.conf
fi

log "Файрвол: дозволено 22/tcp, 80/tcp, 443/tcp+udp"
ufw --force reset >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow 22/tcp  >/dev/null
ufw allow 80/tcp  >/dev/null
ufw allow 443/tcp >/dev/null
ufw allow 443/udp >/dev/null
ufw --force enable >/dev/null

log "fail2ban + автооновлення безпеки"
systemctl enable --now fail2ban
dpkg-reconfigure -f noninteractive unattended-upgrades

log "Готово. Docker: $(docker --version); Compose: $(docker compose version --short)"
echo "Далі: перевірте, що A-запис домену вказує на $(curl -fsS --max-time 5 https://api.ipify.org || echo '<IP сервера>')"
