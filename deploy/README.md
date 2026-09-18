# Розгортання n8n на PostgreSQL (Contabo VPS)

Готовий стек: **n8n** + **PostgreSQL 16** + **Caddy** (автоматичний HTTPS від Let's Encrypt).
Опційно — режим черги з Redis і воркерами.

## Що потрібно

* VPS з Ubuntu 22.04 / 24.04 (підійде будь-який тариф Contabo від 4 GB RAM)
* Домен або субдомен, A-запис якого вказує на IP сервера (напр. `n8n.yourdomain.com`)
* Відкриті порти 80 і 443

---

---

## Крок 0. Домен і DNS

Caddy випускає сертифікат через HTTP-01 challenge: Let's Encrypt стукає на
`http://ваш-домен/.well-known/...` і має потрапити саме на ваш VPS. Тому домен
треба підготувати **до** першого `docker compose up`.

### Варіант А — свій домен (рекомендовано)

Окремий піддомен, не головний сайт: `n8n.вашдомен.com`.

1. У панелі реєстратора (або там, де тримаються NS-записи домену) відкрийте
   керування DNS-зоною.
2. Додайте запис:

   | Тип | Ім'я (Host) | Значення | TTL |
   |---|---|---|---|
   | `A` | `n8n` | `<IP вашого Contabo VPS>` | 300 (або Auto) |

   IP видно в панелі Contabo біля сервера, або на самому сервері: `curl https://api.ipify.org`

3. Зачекайте 5–30 хвилин на поширення.

Якщо DNS домену на **Cloudflare** — на час першого запуску поставте проксі
в режим **DNS only** (сіра хмарка). Після того як сертифікат випустився, можна
увімкнути помаранчеву хмарку, але обов'язково з **SSL/TLS mode = Full (strict)**,
інакше буде цикл редіректів. Режим *Flexible* з n8n не працює.

### Варіант Б — без домену взагалі, автоматично

Домену немає й купувати зараз не хочеться? Нічого реєструвати не треба:

```bash
./scripts/detect-domain.sh
```

Скрипт сам підбере робоче ім'я для цього сервера:

1. **Хостнейм провайдера.** Contabo прописує кожному VPS зворотний DNS виду
   `vmi1234567.contaboserver.net`, і це ім'я вже резолвиться на ваш IP.
   Якщо PTR на місці — скрипт візьме його.
2. **sslip.io.** Якщо PTR немає — буде `<ваш-ip-через-дефіси>.sslip.io`,
   напр. `203-0-113-45.sslip.io`. Це публічний wildcard-DNS: будь-яке таке
   ім'я завжди резолвиться у відповідний IP. Реєстрація не потрібна,
   Let's Encrypt видає на нього звичайний сертифікат.

Одразу з генерацією `.env`:

```bash
./scripts/init-env.sh auto ваша@пошта.com
```

Обмеження такого домену: він **прив'язаний до IP**. Зміните сервер — зміниться
й адреса, а разом з нею всі webhook-URL. Для тесту й внутрішніх задач нормально;
для інтеграцій, які ви віддаєте клієнту, візьміть свій домен (Варіант А).

### Перехід на свій домен пізніше

```bash
nano .env                  # N8N_DOMAIN=n8n.вашдомен.com
./scripts/check-dns.sh
docker compose up -d       # Caddy випустить новий сертифікат сам
```

Дані, воркфлоу й credentials лишаються на місці — міняється тільки адреса.
Не забудьте оновити URL у зовнішніх сервісах, які шлють вам webhook-и.

### Перевірка перед запуском

```bash
./scripts/check-dns.sh
```

Скрипт звіряє A-запис із реальним IP сервера, ловить увімкнений
Cloudflare-проксі, перевіряє, що порти 80/443 вільні й відкриті в ufw.
Зелено — запускайте `docker compose up -d`.

> Змінили домен уже після запуску? Оновіть `N8N_DOMAIN` у `.env`, потім
> `docker compose up -d` — Caddy випустить новий сертифікат автоматично.
> Не забудьте, що адреси активних webhook-ів зміняться, і їх треба буде
> переприв'язати у зовнішніх сервісах.

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
./scripts/init-env.sh n8n.вашдомен.com ваша@пошта.com   # свій домен
./scripts/init-env.sh auto ваша@пошта.com               # домен підібрати автоматично
```

Скрипт створює `.env` із шаблону: генерує паролі БД і `N8N_ENCRYPTION_KEY`,
підбирає розмір пулу з'єднань під кількість ядер, ставить права `600`.
Наприкінці друкує ключ шифрування — **збережіть його в менеджер паролів**.

Без аргументів скрипт спитає домен і пошту інтерактивно. Решту значень у
`.env` можна не чіпати — дефолти робочі.

Що означають основні змінні:

| Змінна | Що ставити |
|---|---|
| `N8N_DOMAIN` | ваш піддомен, напр. `n8n.company.com` — без `https://` і без слеша. Немає свого — `./scripts/detect-domain.sh` |
| `LETSENCRYPT_EMAIL` | реальна пошта: туди Let's Encrypt пише, якщо сертифікат не оновився |
| `TIMEZONE` | `Europe/Warsaw` — впливає на розклади Cron-нод і час у логах |
| `POSTGRES_DB` / `POSTGRES_USER` | `n8n` / `postgres` — лишіть як є |
| `POSTGRES_PASSWORD` | адмінський пароль БД, генерується скриптом |
| `POSTGRES_NON_ROOT_USER` | `n8n` — від нього працює додаток, лишіть як є |
| `POSTGRES_NON_ROOT_PASSWORD` | пароль додатка, генерується скриптом |
| `DB_POSTGRESDB_POOL_SIZE` | ×2 від кількості ядер, ставить скрипт |
| `N8N_ENCRYPTION_KEY` | генерується скриптом; **після першого запуску не міняти** |
| `N8N_IMAGE_TAG` | `latest`, або конкретна версія (`1.115.2`) для продакшену |
| `EXECUTIONS_DATA_MAX_AGE` | скільки годин тримати історію виконань (336 = 14 днів) |
| `EXECUTIONS_MODE` | `regular`; `queue` — лише якщо вмикаєте воркери |

> ⚠️ `N8N_ENCRYPTION_KEY` — це ключ від усіх збережених credentials (API-токени,
> паролі SMTP, OAuth). Дамп бази без нього не відновити. Тримайте копію поза сервером.

## Крок 3. Запуск

```bash
./scripts/check-dns.sh          # preflight: DNS + порти
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
| Підібрати домен | `./scripts/detect-domain.sh` |
| Створити/перезібрати .env | `./scripts/init-env.sh` |
| Перевірка DNS/портів | `./scripts/check-dns.sh` |
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

**Сертифікат не випускається** — запустіть `./scripts/check-dns.sh`; найчастіші
причини: A-запис ще не поширився, увімкнений проксі Cloudflare або закритий
порт 80. Логи: `docker compose logs caddy`.

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
