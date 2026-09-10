# Мапінг полів: Realting → Bitrix24 (LEAD)

> Ліва колонка — **припущені** назви полів Realting. Точні назви беруться з реальної відповіді
> `api-export` (див. «Що потрібно зняти з кабінету» в README). Код у ноді
> `Нормалізувати заявки` шукає значення по списку варіантів, тому більшість типових
> назв підхопиться автоматично; решту треба дописати в масив у відповідному рядку.

| Realting (варіанти назв)                   | Канонічне поле | Bitrix24 (crm.lead.add) | Примітка |
|--------------------------------------------|----------------|--------------------------|----------|
| `id`, `order_id`, `uuid`, `number`         | `externalId`   | `UF_CRM_REALTING_ID`     | ключ ідемпотентності, обовʼязковий |
| `created_at`, `date`, `date_create`        | `createdAt`    | → в `COMMENTS`           | час створення на боці Realting |
| `name`, `full_name`, `contact_name`        | `fullName`     | `NAME` + `LAST_NAME`     | розбивається по першому пробілу |
| `phone`, `contact_phone`, `client.phone`   | `phone`        | `PHONE[0].VALUE`         | чистка до `+` і цифр |
| `email`, `contact_email`                   | `email`        | `EMAIL[0].VALUE`         | нижній регістр |
| `message`, `comment`, `text`               | `comment`      | → в `COMMENTS`           | |
| `object_id`, `property_id`, `listing_id`   | `objectId`     | → в `COMMENTS`           | краще винести в окреме UF-поле |
| `object_title`, `title`                    | `objectTitle`  | `TITLE` (суфікс)         | |
| `object_url`, `url`, `link`                | `objectUrl`    | → в `COMMENTS`           | клікабельне посилання на обʼєкт |
| `language`, `lang`                         | `language`     | → в `COMMENTS`           | корисно для маршрутизації на менеджера |
| `utm_source` / `utm_medium` / `utm_campaign` | `utm*`       | `UTM_SOURCE` / `UTM_MEDIUM` / `UTM_CAMPAIGN` | якщо порожні → `realting.com` / `referral` |
| весь оригінальний обʼєкт                   | `raw`          | —                        | лишається в логах виконання n8n |

Константи, які проставляються завжди:

| Поле Bitrix24        | Значення |
|----------------------|----------|
| `SOURCE_ID`          | `WEB` (можна завести власне джерело `REALTING` — див. `bitrix24-setup.md`) |
| `SOURCE_DESCRIPTION` | `realting.com / <тип заявки>` |
| `ASSIGNED_BY_ID`     | `BITRIX_ASSIGNED_BY_ID` з `.env` |
| `OPENED`             | `Y` |
| `params.REGISTER_SONET_EVENT` | `N` (не спамити живу стрічку порталу) |

## Логіка дедуплікації (3 рівні)

1. **`processedIds` у static data n8n** — заявка, вже відправлена в цьому інстансі, не піде вдруге
   навіть у межах вікна нахлесту.
2. **`crm.lead.list` по `UF_CRM_REALTING_ID`** — захист від повторів після відновлення з бекапу
   чи ручного перезапуску: якщо лід із таким Realting ID уже є, гілка зупиняється.
3. **`crm.duplicate.findbycomm` по телефону (або пошті)** — якщо контакт уже писав раніше з іншої
   форми, новий лід не створюється, а в таймлайн існуючого падає коментар із текстом нової заявки.

Якщо політика продажів інша (потрібен окремий лід на кожне звернення) — досить відключити
гілку 3: у ноді `Дубль за телефоном/поштою?` перевести обидві гілки на `Зібрати поля ліда`.
