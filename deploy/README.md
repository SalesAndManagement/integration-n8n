# Розгортання n8n на PostgreSQL (Contabo VPS)

Готовий стек: **n8n** + **PostgreSQL 16** + **Caddy** (автоматичний HTTPS від Let's Encrypt).
Опційно — режим черги з Redis і воркерами.

## Що потрібно

* VPS з Ubuntu 22.04 / 24.04 (підійде будь-який тариф Contabo від 4 GB RAM)
* Домен або субдомен, A-запис якого вказує на IP сервера (напр. `n8n.yourdomain.com`)
* Відкриті порти 80 і 443

---

## Крок 1. Підготовка сервера

Підключіться по SSH і виконайте:

```bash
ssh root@<IP-вашого-VPS>

apt-get update && apt-get install -y git
git clone https://github.com/SalesAndManagement/integration-n8n.git /opt/n8n
cd /opt/n8n/deploy

bash scripts/bootstrap-server.sh
```

Скрипт встановить Docker + compose, налаштує ufw (22/80/443), fail2ban,
автооновлення безпеки, 2 GB swap і обмежить розмір docker-логів.

## Крок 2. Конфігурація

```bash
cp .env.example .env

# згенерувати секрети
echo "N8N_ENCRYPTION_KEY=$(openssl rand -hex 32)"
echo "POSTGRES_PASSWORD=$(openssl rand -base64 24)"
echo "POSTGRES_NON_ROOT_PASSWORD=$(openssl rand -base64 24)"

nano .env   # вставте згенероване + впишіть N8N_DOMAIN і LETSENCRYPT_EMAIL
```

Обов'язково до заповнення: `N8N_DOMAIN`, `LETSENCRYPT_EMAIL`,
`POSTGRES_PASSWORD`, `POSTGRES_NON_ROOT_PASSWORD`, `N8N_ENCRYPTION_KEY`.

> ⚠️ **`N8N_ENCRYPTION_KEY` після першого запуску не змінювати.** Ним зашифровані
> всі credentials у базі. Збережіть його в менеджері паролів окремо від сервера.

## Крок 3. Запуск

```bash
docker compose up -d
docker compose ps
docker compose logs -f n8n     # Ctrl+C щоб вийти
```

Перший старт займає 1–3 хвилини: n8n прогонить міграції БД, Caddy випустить
сертифікат. Далі відкрийте `https://ваш-домен` і створіть акаунт власника —
перший зареєстрований користувач стає адміністратором.

## Крок 4. Імпорт воркфлоу з цього репозиторію

У каталозі `workflows/` лежить понад 2000 готових `.json`. Імпорт по одному —
через UI (☰ → *Import from File*). Масовий імпорт з диска сервера:

```bash
docker compose cp ../workflows n8n:/tmp/workflows
docker compose exec n8n n8n import:workflow --separate --input=/tmp/workflows
docker compose restart n8n
```

Після імпорту перевірте credentials і webhook-URL у кожному воркфлоу —
вони не переносяться разом з json.

---

## Щоденна експлуатація

| Дія | Команда |
|---|---|
| Статус | `docker compose ps` |
| Логи | `docker compose logs -f n8n` |
| Перезапуск | `docker compose restart n8n` |
| Оновлення n8n | `./scripts/update.sh` |
| Бекап | `./scripts/backup.sh` |
| Відновлення | `./scripts/restore.sh backups/n8n-db-<дата>.dump` |
| Зупинка | `docker compose down` (дані у volume лишаються) |

### Бекап за розкладом

```bash
crontab -e
# щодня о 03:30
30 3 * * * cd /opt/n8n/deploy && ./scripts/backup.sh >> /var/log/n8n-backup.log 2>&1
```

Бекапи лежать у `deploy/backups/` (у git не потрапляють). Копіюйте їх
за межі сервера — Contabo-снапшот не рятує від помилки в самому додатку.

---

## Режим черги (коли одного процесу мало)

Якщо воркфлоу важкі або їх багато паралельно:

```bash
sed -i 's/^EXECUTIONS_MODE=regular/EXECUTIONS_MODE=queue/' .env
docker compose --profile queue up -d
```

Додасться Redis і окремий воркер-контейнер. Кількість воркерів:

```bash
docker compose --profile queue up -d --scale n8n-worker=3
```

Повернутись назад: `EXECUTIONS_MODE=regular` + `docker compose --profile queue down` і `docker compose up -d`.

---

## Архітектура

```
Інтернет ──443──▶ Caddy ──▶ n8n:5678 ──▶ postgres:5432
                              │              (мережа internal,
                              └─ volume .n8n  назовні не видно)
```

* PostgreSQL **не публікує** порт назовні — доступ лише з внутрішньої docker-мережі.
* n8n працює від окремого користувача БД (`n8n`), не від суперюзера.
* Дані: томи `postgres_data`, `n8n_data`, `n8n_files`, `caddy_data`.

## Якщо щось не працює

**Сертифікат не випускається** — перевірте `dig +short ваш-домен` (має бути IP
сервера) і що порт 80 відкритий: `ufw status`. Логи: `docker compose logs caddy`.

**n8n не стартує, в логах помилка БД** — перевірте, що пароль у `.env` збігається
з тим, з яким ініціалізувалась база. Якщо змінили `POSTGRES_NON_ROOT_PASSWORD`
після першого запуску, змініть його і всередині:
```bash
docker compose exec postgres psql -U postgres -c "ALTER USER n8n WITH PASSWORD 'новий';"
```

**Webhook-адреси показують localhost** — не заповнений `N8N_DOMAIN`;
виправте `.env` і `docker compose up -d`.

**«Your credentials cannot be decrypted»** — змінився `N8N_ENCRYPTION_KEY`.
Поверніть старий ключ у `.env` і перезапустіть.
