"""Мінімальний HTTP-клієнт на urllib: ретраї, бекоф, розбір JSON/XML."""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
USER_AGENT = "realting-sync/1.0 (+https://github.com/SalesAndManagement/integration-n8n)"


class HttpError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


@dataclass
class Response:
    status: int
    body: bytes
    content_type: str

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8", errors="replace") or "null")

    def parsed(self) -> Any:
        """JSON, якщо це JSON; інакше XML → вкладені dict/list; інакше сирий текст."""
        text = self.body.decode("utf-8", errors="replace").strip()
        if not text:
            return None
        ctype = self.content_type.lower()
        if "json" in ctype or text[0] in "[{":
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                pass
        if "xml" in ctype or text.startswith("<"):
            try:
                return xml_to_data(ET.fromstring(text))
            except ET.ParseError as exc:
                raise HttpError(f"не вдалося розібрати XML-відповідь: {exc}", self.status, text[:500]) from exc
        raise HttpError("відповідь не є ні JSON, ні XML", self.status, text[:500])


def xml_to_data(element: ET.Element) -> Any:
    """Перетворює XML-дерево на dict/list. Однакові теги-сусіди стають списком."""
    children = list(element)
    if not children:
        text = (element.text or "").strip()
        return {**element.attrib, "value": text} if element.attrib else text

    result: dict[str, Any] = dict(element.attrib)
    for child in children:
        value = xml_to_data(child)
        if child.tag in result:
            existing = result[child.tag]
            if isinstance(existing, list):
                existing.append(value)
            else:
                result[child.tag] = [existing, value]
        else:
            result[child.tag] = value
    return result


def request(
    url: str,
    *,
    method: str = "GET",
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    json_body: Any | None = None,
    timeout: int = 30,
    retries: int = 3,
    backoff: float = 2.0,
    sleep=time.sleep,
) -> Response:
    """Виконує запит із ретраями на мережевих помилках і 5xx/429."""
    if params:
        clean = {k: v for k, v in params.items() if v is not None and v != ""}
        if clean:
            separator = "&" if urllib.parse.urlparse(url).query else "?"
            url = f"{url}{separator}{urllib.parse.urlencode(clean)}"

    data = None
    all_headers = {"Accept": "application/json", "User-Agent": USER_AGENT, **(headers or {})}
    if json_body is not None:
        data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        all_headers.setdefault("Content-Type", "application/json")

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, data=data, headers=all_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return Response(resp.status, resp.read(), resp.headers.get("Content-Type", ""))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code in RETRY_STATUSES and attempt < retries:
                last_error = HttpError(f"HTTP {exc.code}", exc.code, body)
                log.warning("HTTP %s від %s, спроба %s/%s", exc.code, url.split("?")[0], attempt, retries)
            else:
                raise HttpError(f"HTTP {exc.code} від {url.split('?')[0]}: {body[:500]}", exc.code, body) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt >= retries:
                raise HttpError(f"мережева помилка при зверненні до {url.split('?')[0]}: {exc}") from exc
            last_error = exc
            log.warning("мережева помилка (%s), спроба %s/%s", exc, attempt, retries)
        sleep(backoff ** attempt)

    raise HttpError(f"вичерпано спроби для {url.split('?')[0]}: {last_error}")
