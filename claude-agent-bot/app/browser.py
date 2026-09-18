"""Playwright MCP — справжній браузер для агента (кліки, форми, логіни, скріншоти)."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from .config import Settings

log = logging.getLogger(__name__)

MCP_SERVER_NAME = "playwright"
# Каталог, куди Playwright складає снапшоти сторінок і скріншоти.
# Лежить у робочій папці агента, щоб він міг прочитати їх інструментом Read.
OUTPUT_SUBDIR = ".playwright-mcp"


def build_playwright_server(settings: Settings) -> dict[str, Any]:
    """Конфіг stdio-сервера Playwright MCP для ClaudeAgentOptions.mcp_servers."""
    output_dir = settings.workspace / OUTPUT_SUBDIR
    output_dir.mkdir(parents=True, exist_ok=True)

    flags: list[str] = [
        # Бандлений chromium від Playwright. Без цього MCP шукає системний Google Chrome.
        "--browser", "chromium",
        "--viewport-size", settings.browser_viewport,
        "--output-dir", str(output_dir),
        "--caps", settings.browser_caps,
    ]
    if settings.browser_headless:
        flags.append("--headless")
    if settings.browser_no_sandbox:
        # У контейнері під non-root користувачем без CAP_SYS_ADMIN пісочниця chromium не піднімається.
        flags.append("--no-sandbox")

    if settings.browser_persist_profile:
        profile_dir: Path = settings.browser_profile_dir
        profile_dir.mkdir(parents=True, exist_ok=True)
        flags += ["--user-data-dir", str(profile_dir)]
    else:
        flags.append("--isolated")

    if settings.browser_mcp_cli:
        # Встановлення без root: пакет лежить у домашній папці, запускаємо його cli.js напряму.
        command, args = settings.browser_node, [settings.browser_mcp_cli, *flags]
    elif settings.browser_mcp_command == "npx":
        command, args = "npx", ["-y", settings.browser_mcp_package, *flags]
    else:
        # Пакет уже в PATH (глобальний npm install у Docker-образі).
        command, args = settings.browser_mcp_command, flags

    log.info("Playwright MCP: %s %s", command, " ".join(args))
    return {"type": "stdio", "command": command, "args": args, "env": _server_env(settings)}


def _server_env(settings: Settings) -> dict[str, str]:
    """Оточення для процесу браузера.

    Передаємо явно те, без чого він не стартує: шлях до бібліотек, розпакованих
    без root (scripts/install-browser-libs.sh), і шлях до самих браузерів. PATH і
    HOME — на випадок, якщо цей env замінює оточення, а не доповнює його.
    """
    env: dict[str, str] = {}
    if settings.browser_ld_library_path:
        env["LD_LIBRARY_PATH"] = settings.browser_ld_library_path
    for name in ("PLAYWRIGHT_BROWSERS_PATH", "PATH", "HOME"):
        value = os.environ.get(name)
        if value:
            env[name] = value
    return env
