"""Точка входу: збирає конфіг, агента й бота та запускає long polling."""

from __future__ import annotations

import logging
import os
import sys

from .agent import ClaudeAgent
from .bot import TelegramBot
from .config import ConfigError, Settings


def configure_logging() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def main() -> int:
    configure_logging()
    log = logging.getLogger("claude-agent-bot")

    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        log.error("%s", exc)
        return 1

    agent = ClaudeAgent(settings)
    application = TelegramBot(settings, agent).build()

    log.info(
        "Старт: модель=%s, каталог=%s, дозволених користувачів=%d",
        settings.model,
        settings.workspace,
        len(settings.allowed_user_ids),
    )
    application.run_polling(drop_pending_updates=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
