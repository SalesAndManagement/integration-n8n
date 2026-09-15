"""Конфігурація сервісу: читається зі змінних оточення (або .env-файлу)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_ENV_FILE = "/etc/realting-sync.env"


class ConfigError(RuntimeError):
    """Конфігурація неповна або суперечлива."""


def load_env_file(path: str | os.PathLike[str]) -> dict[str, str]:
    """Розбирає простий KEY=VALUE файл. Порожні рядки й '#' ігноруються."""
    values: dict[str, str] = {}
    p = Path(path)
    if not p.is_file():
        return values
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _header_safe(value: str, key: str) -> str:
    """HTTP-заголовки передаються в latin-1. Кирилиця в токені майже завжди
    означає, що в конфіг скопіювали текст-заповнювач, а не реальний ключ."""
    try:
        value.encode("latin-1")
    except UnicodeEncodeError:
        raise ConfigError(
            f"{key} містить нелатинські символи ({value[:20]!r}…) — схоже, у конфіг потрапив "
            f"текст-заповнювач замість справжнього ключа"
        ) from None
    return value


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class RealtingConfig:
    url: str
    token: str
    auth_mode: str = "bearer"          # bearer | header | query | basic | none
    auth_header: str = "X-Api-Key"     # для auth_mode=header
    auth_query_param: str = "token"    # для auth_mode=query
    param_date_from: str = "date_from"
    param_date_to: str = "date_to"
    param_page: str = "page"
    param_limit: str = "limit"
    page_size: int = 200
    pagination: str = "page"           # page | none
    max_pages: int = 50
    extra_params: dict[str, str] = field(default_factory=dict)
    date_format: str = "%Y-%m-%d %H:%M:%S"
    timeout: int = 30


@dataclass(frozen=True)
class WebhookConfig:
    """Приймач хуків від Realting."""

    host: str = "127.0.0.1"
    port: int = 8080
    path: str = "/realting/webhook"
    token: str = ""
    # як Realting підтверджує себе: query | header | bearer | hmac | none
    auth_mode: str = "query"
    auth_header: str = "X-Api-Key"
    query_param: str = "token"
    hmac_header: str = "X-Signature"
    hmac_algorithm: str = "sha256"
    max_body_bytes: int = 1_048_576
    worker_interval: float = 5.0


@dataclass(frozen=True)
class BitrixConfig:
    webhook_url: str
    external_id_field: str = "UF_CRM_REALTING_ID"
    assigned_by_id: int = 1
    source_id: str = "WEB"
    timeout: int = 30
    min_interval: float = 0.5          # ≈2 запити/сек — ліміт порталу
    comment_on_duplicate: bool = True


@dataclass(frozen=True)
class Config:
    realting: RealtingConfig
    bitrix: BitrixConfig
    webhook: WebhookConfig = field(default_factory=WebhookConfig)
    state_path: Path = Path("/var/lib/realting-sync/state.db")
    overlap_minutes: int = 15
    first_run_days: int = 7
    field_map_file: Path | None = None
    skip_masked: bool = True
    import_masked: bool = False
    whole_archive: bool = False
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None, env_file: str | None = None) -> "Config":
        merged: dict[str, str] = {}
        merged.update(load_env_file(env_file or os.environ.get("REALTING_SYNC_ENV_FILE", DEFAULT_ENV_FILE)))
        merged.update({k: v for k, v in (env if env is not None else os.environ).items() if v is not None})

        def get(key: str, default: str = "") -> str:
            return (merged.get(key) or default).strip()

        def get_int(key: str, default: int) -> int:
            raw = get(key)
            if not raw:
                return default
            try:
                return int(raw)
            except ValueError as exc:
                raise ConfigError(f"{key} має бути цілим числом, отримано {raw!r}") from exc

        # URL експорту потрібен лише для режиму поллінгу (команда sync);
        # для приймання хуків достатньо WEBHOOK_TOKEN.
        url = get("REALTING_EXPORT_URL")

        auth_mode = get("REALTING_AUTH_MODE", "bearer").lower()
        allowed_modes = {"bearer", "header", "query", "basic", "none"}
        if auth_mode not in allowed_modes:
            raise ConfigError(f"REALTING_AUTH_MODE має бути одним з {sorted(allowed_modes)}, отримано {auth_mode!r}")

        token = _header_safe(get("REALTING_API_TOKEN"), "REALTING_API_TOKEN")
        if url and auth_mode != "none" and not token:
            raise ConfigError("REALTING_API_TOKEN не заданий (або виставте REALTING_AUTH_MODE=none)")

        webhook = get("BITRIX_WEBHOOK_URL").rstrip("/")
        if not webhook:
            raise ConfigError("BITRIX_WEBHOOK_URL не заданий (вхідний вебхук порталу Bitrix24)")
        if "/rest/" not in webhook:
            raise ConfigError("BITRIX_WEBHOOK_URL має мати вигляд https://<портал>.bitrix24.ua/rest/<id>/<token>/")

        extra: dict[str, str] = {}
        for pair in filter(None, (p.strip() for p in get("REALTING_EXTRA_PARAMS").split(","))):
            key, _, value = pair.partition("=")
            if key.strip():
                extra[key.strip()] = value.strip()

        pagination = get("REALTING_PAGINATION", "page").lower()
        if pagination not in {"page", "none"}:
            raise ConfigError("REALTING_PAGINATION має бути 'page' або 'none'")

        webhook_auth = get("WEBHOOK_AUTH_MODE", "query").lower()
        allowed_webhook_modes = {"query", "header", "bearer", "hmac", "none"}
        if webhook_auth not in allowed_webhook_modes:
            raise ConfigError(
                f"WEBHOOK_AUTH_MODE має бути одним з {sorted(allowed_webhook_modes)}, отримано {webhook_auth!r}"
            )
        # WEBHOOK_TOKEN перевіряється не тут, а при старті приймача (команда serve):
        # для sync/drain/check він не потрібен.
        webhook_token = _header_safe(get("WEBHOOK_TOKEN"), "WEBHOOK_TOKEN")

        _header_safe(webhook, "BITRIX_WEBHOOK_URL")
        _header_safe(url, "REALTING_EXPORT_URL")

        webhook_path = get("WEBHOOK_PATH", "/realting/webhook")
        if not webhook_path.startswith("/"):
            raise ConfigError("WEBHOOK_PATH має починатися зі '/'")

        field_map = get("REALTING_FIELD_MAP_FILE")

        return cls(
            realting=RealtingConfig(
                url=url,
                token=token,
                auth_mode=auth_mode,
                auth_header=get("REALTING_AUTH_HEADER", "X-Api-Key"),
                auth_query_param=get("REALTING_AUTH_QUERY_PARAM", "token"),
                param_date_from=get("REALTING_PARAM_DATE_FROM", "date_from"),
                param_date_to=get("REALTING_PARAM_DATE_TO", "date_to"),
                param_page=get("REALTING_PARAM_PAGE", "page"),
                param_limit=get("REALTING_PARAM_LIMIT", "limit"),
                page_size=get_int("REALTING_PAGE_SIZE", 200),
                pagination=pagination,
                max_pages=get_int("REALTING_MAX_PAGES", 50),
                extra_params=extra,
                date_format=get("REALTING_DATE_FORMAT", "%Y-%m-%d %H:%M:%S"),
                timeout=get_int("REALTING_TIMEOUT", 30),
            ),
            bitrix=BitrixConfig(
                webhook_url=webhook,
                external_id_field=get("BITRIX_EXTERNAL_ID_FIELD", "UF_CRM_REALTING_ID"),
                assigned_by_id=get_int("BITRIX_ASSIGNED_BY_ID", 1),
                source_id=get("BITRIX_SOURCE_ID", "WEB"),
                timeout=get_int("BITRIX_TIMEOUT", 30),
                comment_on_duplicate=_bool(get("BITRIX_COMMENT_ON_DUPLICATE"), True),
            ),
            webhook=WebhookConfig(
                host=get("WEBHOOK_HOST", "127.0.0.1"),
                port=get_int("WEBHOOK_PORT", 8080),
                path=webhook_path,
                token=webhook_token,
                auth_mode=webhook_auth,
                auth_header=get("WEBHOOK_AUTH_HEADER", "X-Api-Key"),
                query_param=get("WEBHOOK_QUERY_PARAM", "token"),
                hmac_header=get("WEBHOOK_HMAC_HEADER", "X-Signature"),
                hmac_algorithm=get("WEBHOOK_HMAC_ALGORITHM", "sha256"),
                max_body_bytes=get_int("WEBHOOK_MAX_BODY_BYTES", 1_048_576),
            ),
            state_path=Path(get("STATE_PATH", "/var/lib/realting-sync/state.db")),
            overlap_minutes=get_int("SYNC_OVERLAP_MINUTES", 15),
            first_run_days=get_int("SYNC_FIRST_RUN_DAYS", 7),
            field_map_file=Path(field_map) if field_map else None,
            skip_masked=not _bool(get("IMPORT_MASKED"), False) and _bool(get("SKIP_MASKED"), True),
            import_masked=_bool(get("IMPORT_MASKED"), False),
            whole_archive=_bool(get("SYNC_WHOLE_ARCHIVE"), False),
            log_level=get("LOG_LEVEL", "INFO").upper(),
        )
