"""Клієнт експорту заявок realting.com."""

from __future__ import annotations

import base64
import logging
from datetime import datetime
from typing import Any, Callable

from .config import RealtingConfig
from .httpclient import Response, request
from .normalize import extract_rows

log = logging.getLogger(__name__)


class RealtingClient:
    def __init__(self, config: RealtingConfig, transport: Callable[..., Response] = request) -> None:
        self.config = config
        self._transport = transport

    def _headers(self) -> dict[str, str]:
        cfg = self.config
        if cfg.auth_mode == "bearer":
            return {"Authorization": f"Bearer {cfg.token}"}
        if cfg.auth_mode == "header":
            return {cfg.auth_header: cfg.token}
        if cfg.auth_mode == "basic":
            encoded = base64.b64encode(cfg.token.encode("utf-8")).decode("ascii")
            return {"Authorization": f"Basic {encoded}"}
        return {}

    def _params(self, date_from: datetime, date_to: datetime, page: int) -> dict[str, Any]:
        cfg = self.config
        params: dict[str, Any] = dict(cfg.extra_params)
        if cfg.param_date_from:
            params[cfg.param_date_from] = date_from.strftime(cfg.date_format)
        if cfg.param_date_to:
            params[cfg.param_date_to] = date_to.strftime(cfg.date_format)
        if cfg.pagination == "page":
            if cfg.param_limit:
                params[cfg.param_limit] = cfg.page_size
            if cfg.param_page:
                params[cfg.param_page] = page
        if cfg.auth_mode == "query":
            params[cfg.auth_query_param] = cfg.token
        return params

    def fetch_raw(self, date_from: datetime, date_to: datetime, page: int = 1) -> Any:
        """Одна сторінка експорту як розібрана структура (JSON або XML)."""
        response = self._transport(
            self.config.url,
            method="GET",
            params=self._params(date_from, date_to, page),
            headers=self._headers(),
            timeout=self.config.timeout,
        )
        return response.parsed()

    def fetch_orders(self, date_from: datetime, date_to: datetime) -> list[dict[str, Any]]:
        """Усі заявки за період, з пагінацією до порожньої/неповної сторінки."""
        cfg = self.config
        collected: list[dict[str, Any]] = []
        seen_signatures: set[str] = set()

        for page in range(1, cfg.max_pages + 1 if cfg.pagination == "page" else 2):
            rows = extract_rows(self.fetch_raw(date_from, date_to, page))
            if not rows:
                break

            signature = repr(sorted(str(sorted(r.items(), key=str)) for r in rows))[:2000]
            if signature in seen_signatures:
                # сервер ігнорує параметр сторінки і віддає той самий набір
                log.warning("сторінка %s повторює попередню — зупиняю пагінацію", page)
                break
            seen_signatures.add(signature)

            collected.extend(rows)
            log.debug("сторінка %s: %s заявок", page, len(rows))

            if cfg.pagination == "none" or len(rows) < cfg.page_size:
                break

        return collected
