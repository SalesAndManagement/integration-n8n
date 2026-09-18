"""Самоперевірка: де саме ламається ланцюг ключ → API → Claude Code → інструменти.

Запускати через scripts/diagnose.sh. Нічого не змінює, тільки читає .env і робить
по одному мінімальному запиту на кожному рівні.
"""

from __future__ import annotations

import asyncio
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent import ClaudeAgent  # noqa: E402
from app.config import ConfigError, Settings  # noqa: E402
from app.env_file import load_env_file  # noqa: E402

BOLD = "\033[1m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
OFF = "\033[0m"


def head(title: str) -> None:
    print(f"\n{BOLD}== {title} =={OFF}")


def ok(text: str) -> None:
    print(f"{GREEN}ok{OFF}  {text}")


def bad(text: str) -> None:
    print(f"{RED}!!{OFF}  {text}")


def note(text: str) -> None:
    print(f"{YELLOW}..{OFF}  {text}")


def mask(value: str) -> str:
    return f"{value[:14]}…{value[-4:]}" if len(value) > 20 else "(порожньо)"


def check_settings() -> Settings | None:
    head("1. Налаштування")
    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        bad(str(exc))
        return None

    print(f"    ключ:        {mask(os.environ.get('ANTHROPIC_API_KEY', ''))}")
    print(f"    workspace:   {settings.workspace_id or '(не задано)'}")
    print(f"    модель:      {settings.model} · effort={settings.effort}")
    print(f"    режим:       {settings.mode}")
    print(f"    браузер:     {'увімкнено' if settings.browser_enabled else 'вимкнено'}")
    print(f"    доступ:      id {len(settings.allowed_user_ids)} · @ {len(settings.allowed_usernames)}")
    custom = os.environ.get("ANTHROPIC_CUSTOM_HEADERS")
    if custom:
        print(f"    заголовки:   {custom}")
    ok("конфіг читається")
    return settings


def check_api(settings: Settings) -> bool:
    """Прямий запит до API — відсікає все, що стосується ключа й workspace."""
    head("2. Anthropic API напряму")
    import httpx

    headers = {
        "x-api-key": os.environ.get("ANTHROPIC_API_KEY", ""),
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    if settings.workspace_id:
        headers["anthropic-workspace-id"] = settings.workspace_id

    payload = {
        "model": settings.model,
        "max_tokens": 16,
        "messages": [{"role": "user", "content": "ping"}],
    }
    try:
        response = httpx.post(
            "https://api.anthropic.com/v1/messages", headers=headers, json=payload, timeout=60
        )
    except httpx.HTTPError as exc:
        bad(f"мережа: {exc}")
        return False

    workspace = response.headers.get("anthropic-workspace-id", "—")
    print(f"    HTTP {response.status_code} · workspace у відповіді: {workspace}")
    if response.is_success:
        ok("ключ робочий, кредити списуються з цього workspace")
        return True

    bad(response.text[:600])
    if response.status_code == 400 and "not scoped to a workspace" in response.text:
        note("ключ рівня організації — впиши ANTHROPIC_WORKSPACE_ID або перевипусти ключ у workspace")
    elif response.status_code == 401:
        note("ключ невірний або відкликаний")
    elif response.status_code == 429:
        note("ліміт запитів або скінчились кредити")
    return False


async def check_agent(settings: Settings) -> bool:
    """Той самий шлях, яким ходить бот: Claude Code через SDK."""
    head("3. Claude Code через SDK")
    agent = ClaudeAgent(settings)
    try:
        reply = await agent.ask(chat_id=0, prompt="Відповідай одним словом: працює?")
    except Exception as exc:  # діагностика — показуємо все
        bad(f"{type(exc).__name__}: {exc}")
        cause = exc.__cause__ or exc.__context__
        while cause is not None:
            print(f"    причина: {type(cause).__name__}: {cause}")
            stderr = getattr(cause, "stderr", None)
            if stderr:
                print(f"    stderr: {str(stderr)[:600]}")
            cause = cause.__cause__ or cause.__context__
        if agent._stderr:
            print("    хвіст stderr процесу:")
            for line in agent._stderr:
                print(f"      {line}")
        print()
        traceback.print_exc()
        return False

    ok(f"відповідь: {reply.text[:200]}")
    if reply.cost_usd is not None:
        print(f"    вартість: ${reply.cost_usd:.4f} · кроків: {reply.turns}")
    return True


async def check_browser(settings: Settings) -> None:
    head("4. Браузер")
    if not settings.browser_enabled:
        note("BROWSER_ENABLED=0 — пропускаю")
        return
    agent = ClaudeAgent(settings)
    try:
        reply = await agent.ask(
            chat_id=0,
            prompt="Відкрий https://example.com і напиши лише заголовок сторінки.",
        )
    except Exception as exc:
        bad(f"{type(exc).__name__}: {exc}")
        return
    if "mcp__playwright" in " ".join(reply.tools_used):
        ok(f"браузер працює: {reply.text[:150]}")
    else:
        note(f"агент не скористався браузером. Відповідь: {reply.text[:150]}")


async def main() -> int:
    print(f"{BOLD}Перевірка агента{OFF}")
    load_env_file()

    settings = check_settings()
    if settings is None:
        return 1

    api_ok = check_api(settings)
    agent_ok = await check_agent(settings)
    if agent_ok:
        await check_browser(settings)

    head("Підсумок")
    if api_ok and agent_ok:
        ok("усе працює — можна писати боту")
        return 0
    if not api_ok:
        bad("проблема в ключі або workspace — крок 2 вище")
    elif not agent_ok:
        bad("API відповідає, але падає Claude Code — крок 3 вище")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
