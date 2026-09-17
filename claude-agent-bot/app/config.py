"""Конфігурація сервісу: читається один раз зі змінних оточення."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ALLOWED_TOOLS = "Read,Glob,Grep,WebSearch,WebFetch,mcp__n8n__trigger_workflow"
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
        )
