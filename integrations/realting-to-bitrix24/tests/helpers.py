"""Спільні заглушки для тестів: транспорт без мережі та збірка конфігу."""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from realting_sync.config import BitrixConfig, Config, RealtingConfig
from realting_sync.httpclient import Response
from realting_sync.state import SyncState

# тести запускають і як пакет, і як окремі модулі — глушимо логи в обох випадках
logging.disable(logging.CRITICAL)


def json_response(payload: Any, status: int = 200) -> Response:
    return Response(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json")


class FakeTransport:
    """Записує виклики і віддає заздалегідь підготовлені відповіді."""

    def __init__(self, responder: Callable[[str, dict], Any]) -> None:
        self.responder = responder
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url: str, **kwargs: Any) -> Response:
        self.calls.append({"url": url, **kwargs})
        result = self.responder(url, kwargs)
        return result if isinstance(result, Response) else json_response(result)


def make_config(**overrides: Any) -> Config:
    realting = RealtingConfig(url="https://realting.test/api/orders", token="t0ken", page_size=2)
    bitrix = BitrixConfig(webhook_url="https://portal.bitrix24.test/rest/1/hook", min_interval=0.0)
    base = {"realting": realting, "bitrix": bitrix, "state_path": ":memory:", "first_run_days": 7}
    base.update(overrides)
    return Config(**base)


def memory_state() -> SyncState:
    return SyncState(":memory:")
