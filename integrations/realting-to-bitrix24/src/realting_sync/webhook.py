"""HTTP-приймач хуків Realting.

Принцип: прийняти, записати в чергу, одразу відповісти 200. Доставкою в
Bitrix24 займається воркер — тому недоступність порталу чи помилка мапінгу
не призводять до втрати заявки і не тримають зʼєднання з Realting.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import urllib.parse
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from .config import WebhookConfig
from .httpclient import xml_to_data
from .state import SyncState

log = logging.getLogger(__name__)


class PayloadError(ValueError):
    """Тіло запиту не вдалося розібрати."""


def parse_payload(body: bytes, content_type: str) -> Any:
    """JSON, form-urlencoded або XML → структура Python."""
    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        raise PayloadError("порожнє тіло запиту")

    ctype = (content_type or "").lower()
    if "form-urlencoded" in ctype:
        parsed = urllib.parse.parse_qs(text, keep_blank_values=True)
        flat = {key: values[0] if len(values) == 1 else values for key, values in parsed.items()}
        # деякі платформи кладуть JSON у поле payload/data
        for key in ("payload", "data", "json"):
            value = flat.get(key)
            if isinstance(value, str) and value.strip().startswith(("{", "[")):
                try:
                    return json.loads(value)
                except json.JSONDecodeError:
                    pass
        return flat

    if "xml" in ctype or text.startswith("<"):
        try:
            return xml_to_data(ET.fromstring(text))
        except ET.ParseError as exc:
            raise PayloadError(f"некоректний XML: {exc}") from exc

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise PayloadError(f"некоректний JSON: {exc}") from exc


def verify(config: WebhookConfig, headers: dict[str, str], query: dict[str, list[str]], body: bytes) -> bool:
    """Чи справді запит від Realting. Порівняння — constant-time."""
    mode = config.auth_mode
    if mode == "none":
        return True

    def eq(candidate: str | None) -> bool:
        return bool(candidate) and hmac.compare_digest(candidate, config.token)

    lower = {key.lower(): value for key, value in headers.items()}

    if mode == "query":
        values = query.get(config.query_param) or []
        return any(eq(value) for value in values)
    if mode == "header":
        return eq(lower.get(config.auth_header.lower()))
    if mode == "bearer":
        raw = lower.get("authorization", "")
        return eq(raw[7:].strip() if raw.lower().startswith("bearer ") else None)
    if mode == "hmac":
        received = (lower.get(config.hmac_header.lower()) or "").strip()
        if not received:
            return False
        if "=" in received:                      # напр. "sha256=abcdef..."
            received = received.split("=", 1)[1]
        digest = hmac.new(config.token.encode("utf-8"), body, config.hmac_algorithm).hexdigest()
        return hmac.compare_digest(received.lower(), digest)
    return False


class WebhookHandler(BaseHTTPRequestHandler):
    server_version = "realting-sync"
    sys_version = ""

    # заповнюється у make_server
    config: WebhookConfig
    on_payload: Callable[[str], int]

    def log_message(self, fmt: str, *args: Any) -> None:
        log.debug("%s %s", self.address_string(), fmt % args)

    def _respond(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path in ("/healthz", "/health"):
            self._respond(200, {"status": "ok"})
            return
        if path == self.config.path:
            # багато платформ перевіряють URL звичайним GET перед збереженням
            self._respond(200, {"status": "ok", "hint": "надсилайте заявки методом POST"})
            return
        self._respond(404, {"status": "error", "message": "not found"})

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != self.config.path:
            self._respond(404, {"status": "error", "message": "not found"})
            return

        length = int(self.headers.get("Content-Length") or 0)
        if length > self.config.max_body_bytes:
            self._respond(413, {"status": "error", "message": "payload too large"})
            return
        body = self.rfile.read(length) if length else b""

        headers = {key: value for key, value in self.headers.items()}
        query = urllib.parse.parse_qs(parsed.query)
        if not verify(self.config, headers, query, body):
            log.warning("відхилено запит з %s: підпис/токен не збігається", self.address_string())
            self._respond(401, {"status": "error", "message": "unauthorized"})
            return

        try:
            payload = parse_payload(body, self.headers.get("Content-Type", ""))
        except PayloadError as exc:
            log.warning("відхилено запит з %s: %s", self.address_string(), exc)
            self._respond(400, {"status": "error", "message": str(exc)})
            return

        inbox_id = self.on_payload(json.dumps(payload, ensure_ascii=False))
        log.info("прийнято заявку в чергу #%s (%s байт)", inbox_id, len(body))
        self._respond(200, {"status": "accepted", "id": inbox_id})


def make_server(config: WebhookConfig, state: SyncState) -> ThreadingHTTPServer:
    def on_payload(payload: str) -> int:
        # SQLite-зʼєднання одне на процес — серіалізуємо записи з різних потоків
        with state.lock:
            return state.enqueue(payload, source="webhook")

    handler = type("BoundWebhookHandler", (WebhookHandler,), {"config": config, "on_payload": staticmethod(on_payload)})
    server = ThreadingHTTPServer((config.host, config.port), handler)
    server.daemon_threads = True
    return server
