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
# Для цих значень «оточення сильніше за файл» — типова пастка: у сесії лишився
# експорт старого ключа (`set -a; . ./.env`), і правки у файлі більше ні на що не впливають.
SHADOW_WARN_KEYS = ("ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN")


def find_duplicates(text: str) -> dict[str, int]:
    """Скільки разів кожен ключ трапляється у файлі (лише ті, що більше разу).

    Дублікат — типова помилка при ручному редагуванні: новий рядок дописали,
    старий лишили. Перемагає останній, а це рідко те, чого чекають.
    """
    counts: dict[str, int] = {}
    for key in _keys(text):
        counts[key] = counts.get(key, 0) + 1
    return {key: count for key, count in counts.items() if count > 1}


def _keys(text: str):
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, _ = line.partition("=")
        if sep and key.strip():
            yield key.strip()


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

    duplicates = find_duplicates(text)
    if duplicates:
        for key, count in duplicates.items():
            log.warning(
                "У %s ключ %s трапляється %d рази — діє останній. Прибери зайві рядки.",
                env_path,
                key,
                count,
            )

    loaded = 0
    for key, value in parse_env_file(text).items():
        if key not in os.environ:
            os.environ[key] = value
            loaded += 1
        elif key in SHADOW_WARN_KEYS and os.environ[key] != value:
            log.warning(
                "%s узято з оточення, а не з %s — значення різні. "
                "Якщо правив файл: unset %s і запусти знову.",
                key,
                env_path,
                key,
            )
    if loaded:
        log.info("Підвантажено %d змінних із %s", loaded, env_path)
    return loaded
