"""Конфігурація сервісу: читається один раз зі змінних оточення."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ALLOWED_TOOLS = "Read,Glob,Grep,WebSearch,WebFetch,mcp__n8n__trigger_workflow"
DEFAULT_BROWSER_PACKAGE = "@playwright/mcp@0.0.81"
AGENT_MODES = ("sandbox", "restricted")
DEFAULT_SYSTEM_PROMPT = "Ти — робочий асистент у Telegram. Відповідай стисло й українською."


class ConfigError(RuntimeError):
    """Бракує або зіпсовано обов'язкову змінну оточення."""


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"Не задано {name}. Скопіюй .env.example у .env і заповни його.")
    return value


def _csv(name: str, default: str = "") -> tuple[str, ...]:
    raw = os.getenv(name) or default
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} має бути цілим числом, отримано {raw!r}") from exc


def _float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} має бути числом, отримано {raw!r}") from exc


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    allowed_user_ids: frozenset[int]
    model: str
    effort: str
    max_budget_usd: float
    max_turns: int
    workspace: Path
    state_file: Path
    allowed_tools: tuple[str, ...]
    system_prompt: str
    show_tool_trace: bool
    n8n_webhook_base_url: str
    n8n_webhook_token: str
    mode: str
    browser_enabled: bool
    browser_headless: bool
    browser_no_sandbox: bool
    browser_persist_profile: bool
    browser_profile_dir: Path
    browser_viewport: str
    browser_caps: str
    browser_mcp_command: str
    browser_mcp_package: str
    browser_mcp_cli: str
    browser_node: str
    browser_ld_library_path: str

    @property
    def is_sandbox(self) -> bool:
        """У sandbox-режимі агент має всі інструменти Claude Code в межах контейнера."""
        return self.mode == "sandbox"

    @classmethod
    def from_env(cls) -> "Settings":
        # SDK читає ключ із оточення процесу сам; тут лише раніша й зрозуміліша помилка.
        _required("ANTHROPIC_API_KEY")

        raw_ids = _csv("TELEGRAM_ALLOWED_USER_IDS")
        if not raw_ids:
            raise ConfigError(
                "TELEGRAM_ALLOWED_USER_IDS порожній. У агента є доступ до файлів і мережі, "
                "тому бот не стартує без явного списку дозволених користувачів."
            )
        try:
            user_ids = frozenset(int(uid) for uid in raw_ids)
        except ValueError as exc:
            raise ConfigError(f"TELEGRAM_ALLOWED_USER_IDS має містити числові id: {exc}") from exc

        workspace = Path(os.getenv("AGENT_WORKSPACE", "./workspace")).expanduser().resolve()
        effort = os.getenv("CLAUDE_EFFORT", "medium").strip() or "medium"
        allowed_effort = {"low", "medium", "high", "xhigh", "max"}
        if effort not in allowed_effort:
            raise ConfigError(f"CLAUDE_EFFORT має бути одним із {sorted(allowed_effort)}, отримано {effort!r}")

        mode = os.getenv("AGENT_MODE", "sandbox").strip().lower() or "sandbox"
        if mode not in AGENT_MODES:
            raise ConfigError(f"AGENT_MODE має бути одним із {list(AGENT_MODES)}, отримано {mode!r}")

        data_dir = workspace.parent
        return cls(
            telegram_token=_required("TELEGRAM_BOT_TOKEN"),
            allowed_user_ids=user_ids,
            model=os.getenv("CLAUDE_MODEL", "claude-opus-5").strip() or "claude-opus-5",
            effort=effort,
            max_budget_usd=_float("MAX_BUDGET_USD", 0.5),
            max_turns=_int("MAX_TURNS", 20),
            workspace=workspace,
            state_file=Path(os.getenv("STATE_FILE", str(workspace.parent / "sessions.json"))).expanduser(),
            allowed_tools=_csv("ALLOWED_TOOLS", DEFAULT_ALLOWED_TOOLS),
            system_prompt=os.getenv("SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT).strip() or DEFAULT_SYSTEM_PROMPT,
            show_tool_trace=_bool("SHOW_TOOL_TRACE", True),
            n8n_webhook_base_url=os.getenv("N8N_WEBHOOK_BASE_URL", "").strip().rstrip("/"),
            n8n_webhook_token=os.getenv("N8N_WEBHOOK_TOKEN", "").strip(),
            mode=mode,
            browser_enabled=_bool("BROWSER_ENABLED", True),
            browser_headless=_bool("BROWSER_HEADLESS", True),
            browser_no_sandbox=_bool("BROWSER_NO_SANDBOX", True),
            browser_persist_profile=_bool("BROWSER_PERSIST_PROFILE", True),
            browser_profile_dir=Path(
                os.getenv("BROWSER_PROFILE_DIR", str(data_dir / "browser-profile"))
            ).expanduser(),
            browser_viewport=os.getenv("BROWSER_VIEWPORT", "1280x720").strip() or "1280x720",
            browser_caps=os.getenv("BROWSER_CAPS", "vision,pdf").strip() or "vision,pdf",
            browser_mcp_command=os.getenv("BROWSER_MCP_COMMAND", "npx").strip() or "npx",
            browser_mcp_package=os.getenv("BROWSER_MCP_PACKAGE", DEFAULT_BROWSER_PACKAGE).strip()
            or DEFAULT_BROWSER_PACKAGE,
            browser_mcp_cli=os.getenv("BROWSER_MCP_CLI", "").strip(),
            browser_node=os.getenv("BROWSER_NODE", "node").strip() or "node",
            browser_ld_library_path=os.getenv("BROWSER_LD_LIBRARY_PATH", "").strip(),
        )
