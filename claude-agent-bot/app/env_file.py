"""Читання .env без зовнішніх залежностей.

SDK не читає .env сам, а `source .env` ламається на значеннях із пробілами
(наприклад SYSTEM_PROMPT). Тому розбираємо файл самі, ще до створення Settings.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

QUOTES = ("'", '"')


def parse_env_file(text: str) -> dict[str, str]:
    """KEY=VALUE по рядках. Порожні рядки й коментарі ігноруються."""
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in QUOTES:
            value = value[1:-1]
        values[key] = value
    return values


def load_env_file(path: str | Path | None = None) -> int:
    """Підвантажує .env у os.environ. Уже задані змінні мають пріоритет.

    Так само працює і в Docker: там значення приходять з env_file/environment,
    і файл нічого не перетирає.
    """
    env_path = Path(path or os.getenv("ENV_FILE") or ".env")
    if not env_path.is_file():
        return 0

    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("Не вдалося прочитати %s: %s", env_path, exc)
        return 0

    loaded = 0
    for key, value in parse_env_file(text).items():
        if key not in os.environ:
            os.environ[key] = value
            loaded += 1
    if loaded:
        log.info("Підвантажено %d змінних із %s", loaded, env_path)
    return loaded
