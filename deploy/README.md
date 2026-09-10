# n8n production stack (Docker + Postgres + Redis queue mode)

Продакшн-розгортання n8n на чистому VPS (Contabo/Hetzner/будь-який Debian 12 / Ubuntu 22.04+).

## Що піднімається

| Сервіс | Роль |
|---|---|
| `postgres` | Postgres 16, тюнінг під RAM сервера, healthcheck |
| `redis` | черга Bull (queue mode), AOF-персистентність, пароль |
| `n8n` | головний інстанс: UI, REST API, schedule/poll-тригери |
| `n8n-worker` × N | виконують воркфлоу (`--concurrency=10` кожен) |
| `n8n-webhook` × N | окремо обробляють продакшн-вебхуки |
| `caddy` | реверс-проксі + автоматичний Let's Encrypt TLS |
| `autoheal` | рестартує будь-який контейнер, що став `unhealthy` |
| `backup` | щоночі `pg_dump` у `./backups`, ротація 14 днів |

## Установка

```bash
# DNS: A-запис n8n.example.com -> IP сервера (зробити ДО запуску)
apt-get update && apt-get install -y git
git clone <repo> /opt/n8n-src
bash /opt/n8n-src/deploy/install.sh n8n.example.com you@example.com
```

Скрипт: оновлює систему, ставить swap, тюнить sysctl/ulimits, ставить Docker,
налаштовує ufw + fail2ban, генерує `.env` із секретами, ставить systemd-юніти
(`n8n.service` + `n8n-watchdog.timer`) і піднімає стек.

Стек ставиться в `/opt/n8n`. Секрети — в `/opt/n8n/.env` (chmod 600).

### Без домену

Якщо домену ще немає, передай `auto` замість імені:

```bash
bash deploy/install.sh auto you@example.com
```

Скрипт визначить публічний IP сервера і збере адресу виду
`n8n.37-60-231-103.sslip.io`. Сервіс sslip.io резолвить будь-яке таке ім'я
в закодований у ньому IP, тому Let's Encrypt видає на нього справжній
сертифікат — HTTPS і OAuth-креденшели працюють одразу, без DNS-записів.

Коли з'явиться реальний домен — постав A-запис на IP (див. `DNS.md`) і заміни
адресу в `.env`:

```bash
cd /opt/n8n
D=n8n.example.com
sed -i "s|^DOMAIN=.*|DOMAIN=$D|" .env
sed -i "s|^N8N_HOST=.*|N8N_HOST=$D|" .env
sed -i "s|^N8N_EDITOR_BASE_URL=.*|N8N_EDITOR_BASE_URL=https://$D|" .env
sed -i "s|^WEBHOOK_URL=.*|WEBHOOK_URL=https://$D/|" .env
docker compose up -d
```

Caddy візьме новий сертифікат сам. Врахуй, що URL уже створених вебхуків
зміняться — їх треба переоновити в зовнішніх сервісах.

> **`N8N_ENCRYPTION_KEY` треба зберегти окремо.** Без нього збережені креденшели
> не розшифруються після переустановки.

## Самопідйом (три рівні)

1. `restart: unless-stopped` — рестарт контейнера при краші/ребуті.
2. `autoheal` — рестарт при `unhealthy` (healthcheck кожні 30 с).
3. `n8n-watchdog.timer` — кожні 2 хв: HTTP `/healthz`, наявність живих воркерів,
   підйом exited-контейнерів, чистка диска при >90%.

Плюс `n8n.service` (`WantedBy=multi-user.target`) — стек піднімається при завантаженні ОС.

## Масштабування

```bash
cd /opt/n8n
sed -i 's/^WORKER_REPLICAS=.*/WORKER_REPLICAS=6/' .env
docker compose up -d
```

## Бекап / відновлення

```bash
# ручний бекап
docker compose exec -T postgres pg_dump -U n8n n8n | gzip > backups/manual-$(date +%F).sql.gz

# відновлення
gunzip -c backups/n8n-20260101-033000.sql.gz | docker compose exec -T postgres psql -U n8n -d n8n
```

## Оновлення n8n

```bash
cd /opt/n8n
sed -i 's/^N8N_VERSION=.*/N8N_VERSION=1.90.2/' .env   # пінити версію
docker compose pull && docker compose up -d
```

## Траблшутинг

### `no pg_hba.conf entry for host ... no encryption`

Постгрес упав під час первинної ініціалізації (найчастіше — невалідне значення
в `PG_*` змінних), тому `initdb` створив каталог даних, але не встиг дописати
правила доступу і створити базу. Наступні запуски бачать непорожній каталог,
пишуть `Skipping initialization` і працюють з напівготовим станом.

Спочатку виправ причину падіння (`docker compose logs postgres`), потім скинь том:

```bash
cd /opt/n8n
docker compose down
docker volume rm n8n_pg_data     # перевір ім'я через docker volume ls
docker compose up -d
```

**Том можна видаляти тільки якщо n8n жодного разу не стартував.** Якщо в базі вже
є воркфлоу — спочатку зніми дамп із `./backups` або через `pg_dump`.

### `invalid value for parameter "shared_buffers"`

Postgres приймає одиниці `B`, `kB`, `MB`, `GB`, `TB` — не `M`/`G`. Перевір
`PG_SHARED_BUFFERS` і `PG_EFFECTIVE_CACHE` в `.env`.

### `getaddrinfo EAI_AGAIN postgres`

Наслідок, а не причина: контейнер `postgres` перезапускається і його імені немає
в DNS докер-мережі. Дивись `docker compose logs postgres`.
