"""Обгортка над Claude Agent SDK: одна сесія на чат, політика дозволів, бюджет."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TextBlock,
    ToolPermissionContext,
    ToolUseBlock,
    query,
)

from .browser import MCP_SERVER_NAME as BROWSER_SERVER_NAME
from .browser import build_playwright_server
from .config import Settings
from .tools import MCP_SERVER_NAME as N8N_SERVER_NAME
from .tools import build_n8n_server

log = logging.getLogger(__name__)

ProgressCallback = Callable[[str], Awaitable[None]]


@dataclass
class AgentReply:
    """Те, що бот показує користувачу після одного запиту."""

    text: str
    tools_used: list[str] = field(default_factory=list)
    cost_usd: float | None = None
    turns: int = 0
    is_error: bool = False


class ClaudeAgent:
    """Тримає мапу chat_id -> session_id і ганяє через неї запити."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._settings.workspace.mkdir(parents=True, exist_ok=True)
        self._mcp_servers: dict[str, Any] = {N8N_SERVER_NAME: build_n8n_server(settings)}
        if settings.browser_enabled:
            self._mcp_servers[BROWSER_SERVER_NAME] = build_playwright_server(settings)
        self._sessions: dict[int, str] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._load_state()

    # --- стан сесій -------------------------------------------------------

    def _load_state(self) -> None:
        path = self._settings.state_file
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            self._sessions = {int(k): str(v) for k, v in raw.items()}
            log.info("Відновлено %d сесій із %s", len(self._sessions), path)
        except (OSError, ValueError) as exc:
            log.warning("Не вдалося прочитати %s (%s) — стартуємо з чистими сесіями", path, exc)

    def _save_state(self) -> None:
        path = self._settings.state_file
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(self._sessions), encoding="utf-8")
        except OSError as exc:
            log.warning("Не вдалося зберегти сесії у %s: %s", path, exc)

    def session_id(self, chat_id: int) -> str | None:
        return self._sessions.get(chat_id)

    def reset(self, chat_id: int) -> bool:
        """Забути контекст чату. Повертає True, якщо було що забувати."""
        existed = self._sessions.pop(chat_id, None) is not None
        if existed:
            self._save_state()
        return existed

    def _lock(self, chat_id: int) -> asyncio.Lock:
        return self._locks.setdefault(chat_id, asyncio.Lock())

    # --- дозволи ----------------------------------------------------------

    async def _deny_unlisted(
        self,
        tool_name: str,
        input_data: dict[str, Any],
        context: ToolPermissionContext,
    ) -> PermissionResultAllow | PermissionResultDeny:
        """Колбек викликається лише для того, чого немає в allowed_tools, — тож відмовляємо.

        Так агент отримує зрозумілу відмову й продовжує роботу замість того, щоб
        зависнути на запиті дозволу, на який у headless-режимі нікому відповісти.
        """
        log.warning("Відмова інструменту %s (input=%s)", tool_name, list(input_data))
        return PermissionResultDeny(
            message=(
                f"Інструмент {tool_name} вимкнено політикою цього бота. "
                f"Доступні: {', '.join(self._settings.allowed_tools)}."
            )
        )

    # --- запит ------------------------------------------------------------

    def _options(self, chat_id: int) -> ClaudeAgentOptions:
        settings = self._settings
        if settings.is_sandbox:
            # Межа — сам контейнер: усередині нього агент користується всіма інструментами
            # Claude Code без запитів на підтвердження.
            permission_mode: str = "bypassPermissions"
            allowed_tools: list[str] = []
            can_use_tool = None
        else:
            permission_mode = "default"
            allowed_tools = list(settings.allowed_tools)
            can_use_tool = self._deny_unlisted

        return ClaudeAgentOptions(
            model=settings.model,
            effort=settings.effort,
            system_prompt=settings.system_prompt,
            cwd=str(settings.workspace),
            allowed_tools=allowed_tools,
            can_use_tool=can_use_tool,
            permission_mode=permission_mode,
            mcp_servers=self._mcp_servers,
            # Не підтягувати ~/.claude і .claude проєкту: конфіг бота задається тільки тут.
            setting_sources=[],
            max_turns=settings.max_turns,
            max_budget_usd=settings.max_budget_usd,
            resume=self._sessions.get(chat_id),
            stderr=lambda line: log.debug("cli: %s", line.rstrip()),
        )

    async def ask(
        self,
        chat_id: int,
        prompt: str,
        on_progress: ProgressCallback | None = None,
    ) -> AgentReply:
        """Один запит користувача. Паралельні запити одного чату серіалізуються."""
        async with self._lock(chat_id):
            texts: list[str] = []
            tools_used: list[str] = []
            result: ResultMessage | None = None

            async for message in query(prompt=prompt, options=self._options(chat_id)):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            texts.append(block.text)
                        elif isinstance(block, ToolUseBlock):
                            tools_used.append(block.name)
                            if on_progress is not None:
                                await on_progress(block.name)
                elif isinstance(message, ResultMessage):
                    result = message

            if result is None:
                return AgentReply(text="Агент не повернув результату. Дивись логи сервісу.", is_error=True)

            if result.session_id:
                self._sessions[chat_id] = result.session_id
                self._save_state()

            text = (result.result or "\n\n".join(t for t in texts if t.strip())).strip()
            if not text:
                text = self._explain_empty(result)

            return AgentReply(
                text=text,
                tools_used=tools_used,
                cost_usd=result.total_cost_usd,
                turns=result.num_turns,
                is_error=bool(result.is_error),
            )

    def _explain_empty(self, result: ResultMessage) -> str:
        """Порожня відповідь зазвичай означає ліміт або помилку API — кажемо прямо."""
        if result.subtype == "error_max_budget_usd":
            return f"Зупинився на ліміті вартості (MAX_BUDGET_USD={self._settings.max_budget_usd}$)."
        if result.subtype == "error_max_turns":
            return f"Зупинився на ліміті кроків (MAX_TURNS={self._settings.max_turns})."
        if result.api_error_status:
            return f"Помилка API: HTTP {result.api_error_status}."
        if result.errors:
            return "Помилка агента: " + "; ".join(result.errors)
        return "Агент завершив роботу без тексту відповіді."
