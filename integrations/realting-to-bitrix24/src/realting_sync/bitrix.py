"""Клієнт REST API Bitrix24 через вхідний вебхук."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from .config import BitrixConfig
from .httpclient import Response, request
from .normalize import Lead

log = logging.getLogger(__name__)

# Помилки порталу, які мають сенс повторити
RETRYABLE_ERRORS = frozenset({"QUERY_LIMIT_EXCEEDED", "OPERATION_TIME_LIMIT", "INTERNAL_SERVER_ERROR"})


class BitrixError(RuntimeError):
    def __init__(self, method: str, code: str, description: str) -> None:
        super().__init__(f"{method}: {code} — {description}")
        self.method = method
        self.code = code
        self.description = description


class BitrixClient:
    """Тонка обгортка над вебхуком: троттлінг ~2 запити/сек + ретраї."""

    def __init__(
        self,
        config: BitrixConfig,
        transport: Callable[..., Response] = request,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self._transport = transport
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_call = 0.0

    def _throttle(self) -> None:
        elapsed = self._monotonic() - self._last_call
        wait = self.config.min_interval - elapsed
        if wait > 0:
            self._sleep(wait)
        self._last_call = self._monotonic()

    def call(self, method: str, params: dict[str, Any] | None = None, retries: int = 3) -> Any:
        url = f"{self.config.webhook_url}/{method}.json"
        for attempt in range(1, retries + 1):
            self._throttle()
            response = self._transport(
                url,
                method="POST",
                json_body=params or {},
                timeout=self.config.timeout,
                retries=1,          # мережеві ретраї робимо тут, разом із бізнес-помилками
            )
            payload = response.json()
            if isinstance(payload, dict) and payload.get("error"):
                code = str(payload.get("error"))
                description = str(payload.get("error_description", ""))
                if code in RETRYABLE_ERRORS and attempt < retries:
                    pause = 2.0 * attempt
                    log.warning("%s повернув %s — пауза %.1fс (спроба %s/%s)", method, code, pause, attempt, retries)
                    self._sleep(pause)
                    continue
                raise BitrixError(method, code, description)
            return payload.get("result") if isinstance(payload, dict) else payload
        raise BitrixError(method, "RETRIES_EXHAUSTED", "вичерпано спроби")

    # --- операції CRM -------------------------------------------------

    def find_lead(self, external_id: str) -> dict[str, Any] | None:
        """Лід із таким Realting ID разом з ознаками, чи є в ньому контакти."""
        result = self.call(
            "crm.lead.list",
            {
                "filter": {self.config.external_id_field: external_id},
                "select": ["ID", "HAS_PHONE", "HAS_EMAIL"],
            },
        )
        rows = result or []
        return rows[0] if rows else None

    def find_lead_by_external_id(self, external_id: str) -> str | None:
        row = self.find_lead(external_id)
        return str(row["ID"]) if row else None

    def find_duplicate(self, lead: Lead) -> str | None:
        """ID існуючого ліда з тим самим телефоном (або поштою), якщо є."""
        for comm_type, value in (("PHONE", lead.phone), ("EMAIL", lead.email)):
            if not value:
                continue
            result = self.call(
                "crm.duplicate.findbycomm",
                {"entity_type": "LEAD", "type": comm_type, "values": [value]},
            ) or {}
            ids = result.get("LEAD") or []
            if ids:
                return str(ids[0])
        return None

    def add_lead(self, lead: Lead) -> str:
        result = self.call("crm.lead.add", {"fields": self.lead_fields(lead), "params": {"REGISTER_SONET_EVENT": "N"}})
        return str(result)

    def update_lead(self, lead_id: str, fields: dict[str, Any]) -> bool:
        result = self.call(
            "crm.lead.update",
            {"id": int(lead_id), "fields": fields, "params": {"REGISTER_SONET_EVENT": "N"}},
        )
        return bool(result)

    def add_timeline_comment(self, lead_id: str, text: str) -> str:
        result = self.call(
            "crm.timeline.comment.add",
            {"fields": {"ENTITY_ID": int(lead_id), "ENTITY_TYPE": "lead", "COMMENT": text}},
        )
        return str(result)

    # --- підготовка полів ---------------------------------------------

    def lead_fields(self, lead: Lead) -> dict[str, Any]:
        cfg = self.config
        fields: dict[str, Any] = {
            "TITLE": lead_title(lead),
            "NAME": lead.first_name or "Без імені",
            "LAST_NAME": lead.last_name,
            "SOURCE_ID": cfg.source_id,
            "SOURCE_DESCRIPTION": f"realting.com / {lead.source_type}",
            "COMMENTS": lead_comment(lead),
            "ASSIGNED_BY_ID": cfg.assigned_by_id,
            "OPENED": "Y",
            "UTM_SOURCE": lead.utm_source or "realting.com",
            "UTM_MEDIUM": lead.utm_medium or "referral",
        }
        if cfg.external_id_field:
            fields[cfg.external_id_field] = lead.external_id
        if lead.utm_campaign:
            fields["UTM_CAMPAIGN"] = lead.utm_campaign
        # Замасковані значення ("n***@gmail.com") у поля контактів не пишемо ніколи:
        # формально e-mail валідний, але дзвонити/писати нікуди.
        if not lead.masked:
            if lead.phone:
                fields["PHONE"] = [{"VALUE": lead.phone, "VALUE_TYPE": "WORK"}]
            if lead.email:
                fields["EMAIL"] = [{"VALUE": lead.email, "VALUE_TYPE": "WORK"}]
        return fields

    def contact_fields(self, lead: Lead) -> dict[str, Any]:
        """Поля для дозаповнення ліда, коли Realting відкрив контакти."""
        fields: dict[str, Any] = {
            "TITLE": lead_title(lead),
            "NAME": lead.first_name or "Без імені",
            "LAST_NAME": lead.last_name,
            "COMMENTS": lead_comment(lead),
        }
        if lead.phone:
            fields["PHONE"] = [{"VALUE": lead.phone, "VALUE_TYPE": "WORK"}]
        if lead.email:
            fields["EMAIL"] = [{"VALUE": lead.email, "VALUE_TYPE": "WORK"}]
        return fields


def lead_title(lead: Lead) -> str:
    prefix = "🔒 " if lead.masked else ""
    title = f"{prefix}Realting #{lead.external_id}"
    if lead.object_title:
        title = f"{title} — {lead.object_title}"
    return title[:255]


def lead_comment(lead: Lead) -> str:
    lines = [
        ("⚠️ Контакти приховані Realting. Прийміть заявку в роботу в кабінеті realting.com — "
         "після цього телефон і пошта підставляться в цей лід автоматично.") if lead.masked else "",
        f"Повідомлення: {lead.comment}" if lead.comment else "",
        f"Обʼєкт: {lead.object_title}" if lead.object_title else "",
        f"Ціна: {lead.object_price}" if lead.object_price else "",
        f"Тип: {lead.object_type}" if lead.object_type else "",
        f"Посилання: {lead.object_url}" if lead.object_url else "",
        f"ID обʼєкта: {lead.object_id}" if lead.object_id else "",
        f"Регіон клієнта: {lead.region}" if lead.region else "",
        f"Мова заявки: {lead.language}" if lead.language else "",
        f"Статус на Realting: {lead.status}" if lead.status else "",
        f"Створено на Realting: {lead.created_at}" if lead.created_at else "",
        f"Отримано: {lead.received_at}" if lead.received_at and lead.received_at != lead.created_at else "",
        f"Realting ID: {lead.external_id}",
    ]
    return "\n".join(line for line in lines if line)


def duplicate_comment(lead: Lead) -> str:
    lines = [
        f"Нова заявка з realting.com #{lead.external_id}",
        lead.comment,
        lead.object_title,
        lead.object_url,
        f"Створено: {lead.created_at}" if lead.created_at else "",
    ]
    return "\n".join(line for line in lines if line)
