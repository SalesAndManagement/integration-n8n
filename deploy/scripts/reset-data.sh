#!/usr/bin/env bash
# Повне очищення даних n8n і бази — щоб стек піднявся з нуля з поточним .env.
# Потрібно, коли секрети в .env розійшлися з тим, що записано в томах
# ("Mismatching encryption keys", "password authentication failed").
#
# Сертифікати Caddy НЕ чіпаються — щоб не впертись у ліміт Let's Encrypt.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "Буде видалено:"
echo "  • базу PostgreSQL з усіма воркфлоу, credentials та історією"
echo "  • каталог /home/node/.n8n (включно з поточним ключем шифрування)"
echo "  • файли у /files"
echo
echo "Збережеться: сертифікати Caddy, ваш .env"
echo

if docker compose ps -q postgres >/dev/null 2>&1 && [ -n "$(docker compose ps -q postgres 2>/dev/null)" ]; then
  echo "Порада: якщо в базі вже є щось потрібне — спершу ./scripts/backup.sh"
  echo
fi

read -r -p "Видалити дані й підняти стек заново? [yes/NO] " a
[ "$a" = "yes" ] || { echo "Скасовано."; exit 1; }

echo "==> Зупиняю контейнери"
docker compose down

echo "==> Видаляю томи даних"
for v in postgres_data n8n_data n8n_files redis_data; do
  id="$(docker volume ls -q \
        --filter label=com.docker.compose.project=n8n \
        --filter label=com.docker.compose.volume="$v" 2>/dev/null || true)"
  if [ -n "$id" ]; then
    docker volume rm "$id" >/dev/null && echo "    видалено: $id"
  fi
done

echo "==> Піднімаю заново"
docker compose up -d

echo
echo "Готово. Стежити за стартом:  docker compose logs -f n8n"
echo "Перший запуск займе 1-3 хвилини (міграції БД)."
