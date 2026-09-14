"""Нормалізація заявки Realting до єдиної схеми.

Точна структура відповіді api-export наперед невідома (кабінет закритий
авторизацією), тому модуль:
  * розгортає будь-яку обгортку-масив (data / items / orders / result / ...);
  * шукає кожне поле за списком вірогідних назв, включно з вкладеними шляхами;
  * дозволяє перевизначити мапінг JSON-файлом, коли формат стане відомим.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

log = logging.getLogger(__name__)

WRAPPER_KEYS = ("data", "items", "orders", "result", "rows", "requests", "leads", "list", "records")

DEFAULT_FIELD_MAP: dict[str, tuple[str, ...]] = {
    "external_id": ("id", "order_id", "orderId", "uuid", "guid", "number", "request_id"),
    "created_at": ("created_at", "createdAt", "date_create", "dateCreate", "date", "datetime", "time"),
    "full_name": ("name", "full_name", "fullName", "contact_name", "client_name", "user.name", "client.name", "contact.name"),
    "first_name": ("first_name", "firstName"),
    "last_name": ("last_name", "lastName", "surname"),
    "phone": ("phone", "phone_number", "phoneNumber", "contact_phone", "tel", "user.phone", "client.phone", "contact.phone"),
    "email": ("email", "e_mail", "contact_email", "user.email", "client.email", "contact.email"),
    "comment": ("message", "comment", "text", "body", "question", "description", "note"),
    "language": ("lang_code", "lang_title", "language", "lang", "locale"),
    "object_id": ("object_id", "objectId", "property_id", "listing_id", "ad_id", "object.id"),
    "object_title": ("object_title", "objectTitle", "title", "property", "object.title", "listing.title"),
    "object_url": ("object_url", "objectUrl", "url", "link", "object.url", "listing.url"),
    "source_type": ("type", "order_type", "form", "form_type", "source"),
    "region": ("region", "country", "geo"),
    "status": ("status_title", "status", "status_id"),
    "object_price": ("object.price", "price"),
    "object_type": ("object.type_title", "object_type"),
    "received_at": ("received_at", "receivedAt"),
    "utm_source": ("utm_source", "utm.source"),
    "utm_medium": ("utm_medium", "utm.medium"),
    "utm_campaign": ("utm_campaign", "utm.campaign"),
}


@dataclass
class Lead:
    external_id: str
    created_at: str = ""
    first_name: str = ""
    last_name: str = ""
    full_name: str = ""
    phone: str = ""
    email: str = ""
    comment: str = ""
    language: str = ""
    object_id: str = ""
    object_title: str = ""
    object_url: str = ""
    source_type: str = "realting_order"
    region: str = ""
    status: str = ""
    object_price: str = ""
    object_type: str = ""
    received_at: str = ""
    masked: bool = False
    utm_source: str = ""
    utm_medium: str = ""
    utm_campaign: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def has_contact(self) -> bool:
        return bool(self.phone or self.email)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SkippedOrder(ValueError):
    """Заявку неможливо імпортувати (немає ID або контактів)."""

    def __init__(self, reason: str, raw: Any) -> None:
        super().__init__(reason)
        self.reason = reason
        self.raw = raw


def load_field_map(path: str | Path | None) -> dict[str, tuple[str, ...]]:
    """Зливає типовий мапінг із користувацьким JSON {поле: [шляхи...]}."""
    merged = {key: tuple(value) for key, value in DEFAULT_FIELD_MAP.items()}
    if not path:
        return merged
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    for key, value in data.items():
        if key not in merged:
            log.warning("невідоме поле %r у файлі мапінгу — ігнорую", key)
            continue
        paths = (value,) if isinstance(value, str) else tuple(value)
        merged[key] = paths  # користувацькі шляхи мають пріоритет і замінюють типові
    return merged


def _as_rows(value: Any) -> list[dict[str, Any]]:
    """Значення обгортки → список заявок, знімаючи ще один рівень за потреби."""
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        # XML-подібна вкладеність: {"order": {...}} або {"order": [{...}, {...}]}
        if len(value) == 1:
            inner = next(iter(value.values()))
            if isinstance(inner, list):
                return [row for row in inner if isinstance(row, dict)]
            if isinstance(inner, dict):
                return [inner]
        return [value]
    return []


def extract_rows(payload: Any) -> list[dict[str, Any]]:
    """Дістає список заявок із довільної обгортки відповіді."""
    if payload is None:
        return []
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []

    for key in WRAPPER_KEYS:
        if key in payload:
            value = payload[key]
            if isinstance(value, list):
                # порожній список — це «заявок немає», а не обгортка-заявка
                return [row for row in value if isinstance(row, dict)]
            rows = _as_rows(value)
            if rows:
                return rows

    # корінь XML із довільною назвою: {"order": [...]}
    if len(payload) == 1:
        value = next(iter(payload.values()))
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        rows = _as_rows(value)
        if rows:
            return rows

    # {"1": {...}, "2": {...}} — словник заявок за ключами
    values = list(payload.values())
    if len(values) > 1 and all(isinstance(v, dict) for v in values):
        return values

    return [payload]


def dig(source: Any, path: str) -> Any:
    """Значення за шляхом 'a.b.c'; None, якщо його немає."""
    current = source
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if index < len(current) else None
        else:
            return None
        if current is None:
            return None
    return current


def first_value(row: dict[str, Any], paths: Sequence[str]) -> str:
    for path in paths:
        value = dig(row, path)
        if isinstance(value, dict):
            value = value.get("value") or value.get("#text")
        if isinstance(value, (list, tuple)):
            value = next((v for v in value if v not in (None, "")), None)
        if value is None or value == "":
            continue
        if isinstance(value, bool):
            return "1" if value else ""
        return str(value).strip()
    return ""


MASK_MARKER = "***"


def is_masked(*values: str) -> bool:
    """Realting ховає контакти заявок, не прийнятих у роботу: 'Ник***', '+790******21'.

    Такі заявки не можна вантажити в CRM: менеджеру нікуди дзвонити, а зайнятий
    зовнішній ID потім завадить імпортувати ту саму заявку з відкритими контактами.
    """
    return any(MASK_MARKER in (value or "") for value in values)


def clean_phone(value: str) -> str:
    """Лишає цифри та провідний '+'. Порожньо, якщо цифр менше семи."""
    if not value:
        return ""
    digits = re.sub(r"[^\d]", "", value)
    if len(digits) < 7:
        return ""
    return ("+" if value.strip().startswith("+") else "") + digits


def clean_email(value: str) -> str:
    value = value.strip().lower()
    return value if re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", value) else ""


def split_name(full_name: str) -> tuple[str, str]:
    parts = [p for p in re.split(r"\s+", full_name.strip()) if p]
    if not parts:
        return "", ""
    return parts[0], " ".join(parts[1:])


def normalize(row: dict[str, Any], field_map: dict[str, tuple[str, ...]] | None = None) -> Lead:
    """Перетворює сиру заявку на Lead. Кидає SkippedOrder, якщо імпорт неможливий."""
    fmap = field_map or DEFAULT_FIELD_MAP

    external_id = first_value(row, fmap["external_id"])
    if not external_id:
        raise SkippedOrder("немає жодного з полів-ідентифікаторів", row)

    raw_phone = first_value(row, fmap["phone"])
    raw_email = first_value(row, fmap["email"])
    raw_name = first_value(row, fmap["full_name"])
    masked = is_masked(raw_phone, raw_email, raw_name)

    phone = clean_phone(raw_phone)
    email = clean_email(raw_email)
    if not masked and not phone and not email:
        raise SkippedOrder("немає ні телефону, ні e-mail", row)

    first_name = first_value(row, fmap["first_name"])
    last_name = first_value(row, fmap["last_name"])
    if first_name or last_name:
        # окремі поля імені/прізвища точніші за злите — вони й формують full_name
        full_name = " ".join(part for part in (first_name, last_name) if part)
    else:
        full_name = first_value(row, fmap["full_name"])
        first_name, last_name = split_name(full_name)

    return Lead(
        external_id=external_id,
        created_at=first_value(row, fmap["created_at"]),
        first_name=first_name,
        last_name=last_name,
        full_name=full_name,
        phone=phone,
        email=email,
        comment=first_value(row, fmap["comment"]),
        language=first_value(row, fmap["language"]),
        object_id=first_value(row, fmap["object_id"]),
        object_title=first_value(row, fmap["object_title"]),
        object_url=first_value(row, fmap["object_url"]),
        source_type=first_value(row, fmap["source_type"]) or "realting_order",
        region=first_value(row, fmap["region"]),
        status=first_value(row, fmap["status"]),
        object_price=first_value(row, fmap["object_price"]),
        object_type=first_value(row, fmap["object_type"]),
        received_at=first_value(row, fmap["received_at"]),
        masked=masked,
        utm_source=first_value(row, fmap["utm_source"]),
        utm_medium=first_value(row, fmap["utm_medium"]),
        utm_campaign=first_value(row, fmap["utm_campaign"]),
        raw=row,
    )


def normalize_all(
    rows: Iterable[dict[str, Any]],
    field_map: dict[str, tuple[str, ...]] | None = None,
    skip_masked: bool = True,
) -> tuple[list[Lead], list[SkippedOrder]]:
    leads: list[Lead] = []
    skipped: list[SkippedOrder] = []
    seen: set[str] = set()
    for row in rows:
        try:
            lead = normalize(row, field_map)
        except SkippedOrder as exc:
            skipped.append(exc)
            continue
        if lead.masked and skip_masked:
            skipped.append(SkippedOrder(
                "контакти замасковані Realting (заявка не прийнята в роботу)", row
            ))
            continue
        if lead.external_id in seen:          # дубль у межах однієї відповіді
            continue
        seen.add(lead.external_id)
        leads.append(lead)
    return leads, skipped
