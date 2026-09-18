"""Самоперевірка: де саме ламається ланцюг ключ → API → Claude Code → інструменти.

Запускати через scripts/diagnose.sh. Нічого не змінює, тільки читає .env і робить
по одному мінімальному запиту на кожному рівні.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent import ClaudeAgent  # noqa: E402
from app.config import ConfigError, Settings  # noqa: E402
from app.env_file import find_duplicates, load_env_file, parse_env_file  # noqa: E402

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
    # Показуємо більше початку: так видно, чи це справді новий ключ.
    return f"{value[:24]}…{value[-4:]} ({len(value)} символів)" if len(value) > 30 else "(порожньо або обрізаний)"


def check_env_file(before: dict[str, str]) -> None:
    """Дві причини «я ж замінив ключ, а він старий»: дублікат рядка і експорт у сесії."""
    path = pathlib.Path(".env")
    if not path.is_file():
        note(".env не знайдено поруч — беру лише змінні оточення")
        return

    text = path.read_text(encoding="utf-8")
    for key, count in find_duplicates(text).items():
        bad(f"у .env {count} рядки з {key} — діє ОСТАННІЙ. Прибери зайві:")
        print(f"      grep -n '^{key}=' .env")

    # Оточення сильніше за файл: старий експорт у сесії робить правки у .env марними.
    for key, value in parse_env_file(text).items():
        if key in before and before[key] != value:
            bad(f"{key} береться з оточення сесії, а НЕ з .env — значення різні.")
            print(f"      Виправити:  unset {key}")
            print("      Або запусти з чистої сесії: exec bash -l")


def check_settings(before: dict[str, str]) -> Settings | None:
    head("1. Налаштування")
    check_env_file(before)
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
    stray = [name for name, value in headers.items() if not str(value).isascii()]
    if stray:
        bad(f"у значеннях {', '.join(stray)} є нелатинські символи — ключ скопійовано з чимось зайвим")
        return False

    try:
        response = httpx.post(
            "https://api.anthropic.com/v1/messages", headers=headers, json=payload, timeout=60
        )
    except httpx.HTTPError as exc:
        bad(f"мережа: {exc}")
        return False
    except (UnicodeEncodeError, ValueError) as exc:
        bad(f"запит не склався: {exc}")
        return False

    workspace = response.headers.get("anthropic-workspace-id", "—")
    print(f"    HTTP {response.status_code} · workspace у відповіді: {workspace}")
    if response.is_success:
        ok("ключ робочий, кредити списуються з цього workspace")
        return True

    bad(response.text[:600])
    if response.status_code == 400 and "not scoped to a workspace" in response.text:
        note("ключ рівня організації — йому потрібен id workspace")
        suggest_workspaces(headers["x-api-key"])
    elif response.status_code == 401:
        note("ключ невірний або відкликаний")
    elif response.status_code == 429:
        note("ліміт запитів або скінчились кредити")
    return False


def suggest_workspaces(api_key: str) -> None:
    """Ключ рівня організації приймається Admin API — спитаємо в нього список workspace.

    Default Workspace у цьому списку не показується (так задумано), тому якщо
    список порожній, простіше створити окремий workspace для агента.
    """
    import httpx

    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    try:
        response = httpx.get(
            "https://api.anthropic.com/v1/organizations/workspaces",
            headers=headers,
            params={"limit": 20},
            timeout=30,
        )
    except httpx.HTTPError as exc:
        note(f"не вдалося спитати список workspace: {exc}")
        return

    if not response.is_success:
        note(f"Admin API відповів {response.status_code}: {response.text[:200]}")
        note("Створи workspace у Console -> Settings -> Workspaces і візьми його id")
        return

    items = response.json().get("data", [])
    if items:
        print("    доступні workspace:")
        for item in items:
            print(f"      {item.get('id')}  {item.get('name')}")
        print(f"\n    Впиши в .env:  ANTHROPIC_WORKSPACE_ID={items[0].get('id')}")
        return

    note("жодного окремого workspace немає (Default у списку не показується)")
    print("    Створи його однією командою — id буде у відповіді:")
    print(
        '      curl -sS -X POST https://api.anthropic.com/v1/organizations/workspaces \\\n'
        '        -H "x-api-key: $ANTHROPIC_API_KEY" -H "anthropic-version: 2023-06-01" \\\n'
        '        -H "content-type: application/json" -d \'{"name": "Agent"}\''
    )


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
    # Знімок до завантаження: показує, що вже стояло в оточенні й перекриє файл.
    before = dict(os.environ)
    load_env_file()

    settings = check_settings(before)
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
