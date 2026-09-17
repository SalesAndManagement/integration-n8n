"""Власні інструменти агента, віддані через in-process MCP-сервер SDK."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from claude_agent_sdk import create_sdk_mcp_server, tool

from .config import Settings

log = logging.getLogger(__name__)

MCP_SERVER_NAME = "n8n"
REQUEST_TIMEOUT_S = 30.0
MAX_RESULT_CHARS = 4000


def _text(message: str, *, is_error: bool = False) -> dict[str, Any]:
    """Результат у формі, якої чекає MCP."""
    result: dict[str, Any] = {"content": [{"type": "text", "text": message}]}
    if is_error:
        result["is_error"] = True
    return result


def _clean_path(raw: str) -> str:
    """Дозволяємо тільки відносний шлях вебхука — не повний URL і не вихід угору."""
    path = raw.strip().lstrip("/")
    if "://" in path or path.startswith("//"):
        raise ValueError("передай тільки шлях вебхука, без схеми й хоста")
    if ".." in path.split("/"):
        raise ValueError("у шляху не можна використовувати '..'")
    if not path:
        raise ValueError("порожній шлях вебхука")
    return path


def build_n8n_server(settings: Settings):
    """Створює MCP-сервер з інструментами n8n. Замикання тримає налаштування."""

    @tool(
        "trigger_workflow",
        "Запустити n8n workflow через його Webhook-ноду. Приймає шлях вебхука "
        "(наприклад 'webhook/new-lead' або 'webhook-test/new-lead') і JSON-тіло запиту.",
        {"webhook_path": str, "payload_json": str},
    )
    async def trigger_workflow(args: dict[str, Any]) -> dict[str, Any]:
        if not settings.n8n_webhook_base_url:
            return _text("N8N_WEBHOOK_BASE_URL не налаштовано — запускати workflow нікуди.", is_error=True)

        try:
            path = _clean_path(str(args.get("webhook_path", "")))
        except ValueError as exc:
            return _text(f"Некоректний webhook_path: {exc}", is_error=True)

        raw_payload = str(args.get("payload_json") or "{}").strip() or "{}"
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError as exc:
            return _text(f"payload_json не є валідним JSON: {exc}", is_error=True)

        url = f"{settings.n8n_webhook_base_url}/{path}"
        headers = {"Content-Type": "application/json"}
        if settings.n8n_webhook_token:
            headers["Authorization"] = settings.n8n_webhook_token

        log.info("n8n trigger: POST %s", url)
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S) as client:
                response = await client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            return _text(f"Не вдалося звернутись до n8n ({url}): {exc}", is_error=True)

        body = response.text[:MAX_RESULT_CHARS]
        if response.is_error:
            return _text(f"n8n відповів {response.status_code}: {body}", is_error=True)
        return _text(f"n8n відповів {response.status_code}. Тіло відповіді:\n{body or '(порожнє)'}")

    return create_sdk_mcp_server(name=MCP_SERVER_NAME, version="1.0.0", tools=[trigger_workflow])
