# Мапінг полів: Realting → Bitrix24 (LEAD)

> Формат підтверджено бойовою відповіддю `/api/orders/export`.

> Ліва колонка — **припущені** назви полів Realting. Точні назви видно у першому ж
> реальному хуку (`journalctl -u realting-webhook`, або таблиця `inbox` у стані),
> а для режиму поллінгу — командою `realting-sync probe`. Модуль `normalize.py` шукає значення за списком варіантів, тому
> більшість типових назв підхопиться сама; решту не треба правити в коді — допишіть
> власні шляхи у файл `REALTING_FIELD_MAP_FILE` (JSON `{"поле": ["шлях.у.відповіді"]}`).

| Realting (реальне поле)      | Канонічне      | Bitrix24 (crm.lead.add)  | Примітка |
|------------------------------|----------------|--------------------------|----------|
| `id`                         | `external_id`  | `UF_CRM_REALTING_ID`     | ключ ідемпотентності |
| `name`                       | `first_name` + `last_name` | `NAME`, `LAST_NAME` | ділиться по першому пробілу |
| `phone`                      | `phone`        | `PHONE[0].VALUE`         | чистка до `+` і цифр |
| `email`                      | `email`        | `EMAIL[0].VALUE`         | нижній регістр, перевірка формату |
| `message`                    | `comment`      | → в `COMMENTS`           | |
| `region`                     | `region`       | → в `COMMENTS`           | країна клієнта |
| `lang_code` → `lang_title`   | `language`     | → в `COMMENTS`           | `lang_code` буває `null` |
| `status_title`               | `status`       | → в `COMMENTS`           | напр. `In work` |
| `object.id`                  | `object_id`    | → в `COMMENTS`           | |
| `object.title`               | `object_title` | `TITLE` (суфікс)         | |
| `object.url`                 | `object_url`   | → в `COMMENTS`           | |
| `object.price`               | `object_price` | → в `COMMENTS`           | рядком, напр. `$312 455` |
| `object.type_title`          | `object_type`  | → в `COMMENTS`           | `Properties` / `Users` |
| `utm.source` / `.medium` / `.campaign` | `utm_*` | `UTM_SOURCE` / `UTM_MEDIUM` / `UTM_CAMPAIGN` | порожні → `realting.com` / `referral` |
| `created_at`                 | `created_at`   | → в `COMMENTS`           | |
| `received_at`                | `received_at`  | → в `COMMENTS`           | лише якщо відрізняється |
| весь оригінальний обʼєкт     | `raw`          | —                        | у логах при `LOG_LEVEL=DEBUG` |

Конверт відповіді: `{"success": true, "meta": {"page","limit","total","pages"}, "data": [...]}`.
Пагінація йде за `meta.pages`; `{"success": false}` з кодом 200 — це помилка, а не заявки.

## Маскування контактів

Заявки зі статусом на кшталт `Request not accepted to work` приходять із затертими
контактами: `"name": "Ник***"`, `"phone": "+790******21"`, `"email": "n***@gmail.com"`.
Такі заявки **не імпортуються** (`SKIP_MASKED=true`): менеджеру нікуди дзвонити, а зайнятий
`UF_CRM_REALTING_ID` завадив би імпортувати ту саму заявку пізніше, коли контакти відкриються.
Скільки таких — показує `realting-sync probe` у зрізі за статусами.

Константи, які проставляються завжди:

| Поле Bitrix24        | Значення |
|----------------------|----------|
| `SOURCE_ID`          | `WEB` (можна завести власне джерело `REALTING` — див. `bitrix24-setup.md`) |
| `SOURCE_DESCRIPTION` | `realting.com / <тип заявки>` |
| `ASSIGNED_BY_ID`     | `BITRIX_ASSIGNED_BY_ID` з `/etc/realting-sync.env` |
| `OPENED`             | `Y` |
| `params.REGISTER_SONET_EVENT` | `N` (не спамити живу стрічку порталу) |

## Логіка дедуплікації (3 рівні)

1. **SQLite `/var/lib/realting-sync/state.db`** — заявка, вже імпортована сервісом, не піде вдруге
   навіть у межах вікна нахлесту.
2. **`crm.lead.list` по `UF_CRM_REALTING_ID`** — захист від повторів після відновлення з бекапу
   чи запуску з `--force`: якщо лід із таким Realting ID уже є, заявка пропускається.
3. **`crm.duplicate.findbycomm` по телефону (або пошті)** — якщо контакт уже писав раніше з іншої
   форми, новий лід не створюється, а в таймлайн існуючого падає коментар із текстом нової заявки.

Якщо політика продажів інша (потрібен окремий лід на кожне звернення) — вимкніть третій
рівень: `BITRIX_COMMENT_ON_DUPLICATE=false` у `/etc/realting-sync.env`.
