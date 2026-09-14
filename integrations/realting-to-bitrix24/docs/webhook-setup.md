# Налаштування хука в кабінеті Realting

## 1. Що вписати в кабінеті

```
https://<ваш-домен>/realting/webhook?token=<WEBHOOK_TOKEN>
```

`WEBHOOK_TOKEN` — **наш власний секрет**, а не ключ Realting: платформа просто шле POST на ту
адресу, яку ви їй дали, тому токен у самому URL працює незалежно від того, що вона вміє.
Згенеруйте його `openssl rand -hex 24` і покладіть у `/etc/realting-sync.env`.
Якщо Realting додатково підписує запити власним ключем — переведіть перевірку на нього
(таблиця нижче), тоді `WEBHOOK_TOKEN` = ключ із кабінету.
Якщо платформа передає ключ не в URL, а інакше — змініть `WEBHOOK_AUTH_MODE`:

| Як Realting передає ключ | `WEBHOOK_AUTH_MODE` | Додатково |
|--------------------------|---------------------|-----------|
| `?token=abc` в URL (типово) | `query` | `WEBHOOK_QUERY_PARAM` — назва параметра |
| власний заголовок, напр. `X-Api-Key: abc` | `header` | `WEBHOOK_AUTH_HEADER` — назва заголовка |
| `Authorization: Bearer abc` | `bearer` | — |
| підпис тіла, напр. `X-Signature: sha256=…` | `hmac` | `WEBHOOK_HMAC_HEADER`, `WEBHOOK_HMAC_ALGORITHM` |
| ніяк (ендпоінт відкритий) | `none` | небажано — адресу можна підібрати |

Після зміни: `sudo systemctl restart realting-webhook`.

## 2. Домен і TLS

Realting майже напевно вимагатиме `https://`. Варіанти:

- **свій домен** — A-запис `webhook.вашдомен.com → 51.68.138.209`;
- **без домену** — `webhook.51.68.138.209.nip.io` (безкоштовний wildcard-DNS, резолвиться
  в той самий IP; Let's Encrypt видає на нього сертифікат).

```bash
sudo ./deploy/install-caddy.sh webhook.вашдомен.com
curl -s https://webhook.вашдомен.com/healthz     # {"status":"ok"}
```

## 3. Перевірка наскрізь

```bash
curl -s -X POST "https://<домен>/realting/webhook?token=<WEBHOOK_TOKEN>" \
  -H 'Content-Type: application/json' \
  -d '{"id":"test-1","name":"Тест Тестовий","phone":"+380671234567","message":"перевірка"}'
# {"status":"accepted","id":1}

sudo -u realting realting-sync stats      # заявка в черзі
sudo -u realting realting-sync drain      # доставити негайно, не чекаючи воркера
```

У Bitrix24 має зʼявитись лід «Realting #test-1». Після перевірки його можна видалити —
повторний тест із тим самим `id` більше ліда не створить (спрацює дедуп), для нового тесту
змініть `id`.

## 4. Коли прийде перший реальний хук

```bash
journalctl -u realting-webhook -n 50
sudo -u realting sqlite3 /var/lib/realting-sync/state.db \
  "SELECT payload FROM inbox ORDER BY id DESC LIMIT 1;"
```

Це і є справжня структура заявки Realting. Якщо назви полів відрізняються від типових
(`id`, `name`, `phone`, `email`, `message`) — не треба правити код: створіть
`/etc/realting-sync.map.json`

```json
{
  "external_id": ["zayavka_id"],
  "phone": ["kontakt.telefon"],
  "comment": ["tekst"]
}
```

вкажіть його в `REALTING_FIELD_MAP_FILE` і перезапустіть приймач. Записи, які не змапились,
лишаються в черзі зі статусом `skipped` — після виправлення мапінгу їх можна прогнати
повторно (`drain --retry-failed` повертає `failed`, для `skipped` — змініть статус запиту
вручну або надішліть хук ще раз).

## 5. Що бачить інтернет

Назовні відкриті лише 80/443 (Caddy). Сам приймач слухає `127.0.0.1:8080` і недоступний
ззовні. Запит без правильного ключа отримує `401` і в чергу не потрапляє; тіло більше
1 МБ — `413`. Логи Caddy: `/var/log/caddy/realting-webhook.log`.

## 6. Старт «тільки нові заявки»

Експорт Realting віддає весь архів (у нашому випадку 87 заявок, з них 51 з відкритими
контактами і 36 замаскованих). Щоб історія не поїхала в CRM, але й не загубилась:

```bash
sudo -u realting realting-sync seed     # позначає наявні відкриті заявки як оброблені
sudo -u realting realting-sync stats    # має показати «Синхронізовано заявок: 51»
sudo systemctl enable --now realting-sync.timer
```

`seed` **не** позначає замасковані заявки. Це навмисно: коли менеджер прийме таку заявку
в роботу і Realting відкриє контакти, вона приїде в Bitrix24 як нова — попри те, що
`created_at` у неї старий.

Разово довантажити всю історію, якщо колись знадобиться: `realting-sync sync --all`.
