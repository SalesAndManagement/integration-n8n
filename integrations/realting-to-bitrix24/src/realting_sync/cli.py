"""Командний інтерфейс: sync | probe | check | stats."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone

from . import __version__
from .bitrix import BitrixClient, BitrixError
from .config import Config, ConfigError
from .httpclient import HttpError
from .normalize import extract_rows, load_field_map, normalize_all
from .realting import RealtingClient
from .state import SyncState
from .sync import Synchronizer

log = logging.getLogger("realting_sync")


def parse_moment(value: str) -> datetime:
    """'2026-09-01' або '2026-09-01T10:00:00' → aware datetime в UTC."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"не розпізнав дату {value!r} (очікую 2026-09-01 або 2026-09-01T10:00:00)") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="realting-sync",
        description="Імпорт заявок realting.com у Bitrix24 CRM.",
    )
    parser.add_argument("--version", action="version", version=f"realting-sync {__version__}")
    parser.add_argument("--env-file", help=f"файл зі змінними оточення (типово /etc/realting-sync.env)")
    parser.add_argument("--log-level", help="DEBUG | INFO | WARNING | ERROR")
    sub = parser.add_subparsers(dest="command", required=True)

    p_sync = sub.add_parser("sync", help="забрати нові заявки і створити ліди")
    p_sync.add_argument("--since", type=parse_moment, help="почати з цієї дати замість збереженого стану")
    p_sync.add_argument("--until", type=parse_moment, help="кінець періоду (типово — зараз)")
    p_sync.add_argument("--dry-run", action="store_true", help="нічого не писати в Bitrix24, лише показати")
    p_sync.add_argument("--force", action="store_true", help="ігнорувати локальний стан (дедуп у Bitrix24 лишається)")

    p_probe = sub.add_parser("probe", help="показати сиру відповідь Realting і результат мапінгу")
    p_probe.add_argument("--days", type=int, default=7, help="за скільки останніх днів запитати (типово 7)")
    p_probe.add_argument("--raw", action="store_true", help="друкувати повну відповідь без обрізання")

    sub.add_parser("check", help="перевірити конфіг, доступ до Realting і до Bitrix24")
    sub.add_parser("stats", help="стан локальної бази синхронізації")
    return parser


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )


def cmd_sync(config: Config, args: argparse.Namespace) -> int:
    with SyncState(config.state_path) as state:
        syncer = Synchronizer(config, RealtingClient(config.realting), BitrixClient(config.bitrix), state)
        report = syncer.run(since=args.since, until=args.until, dry_run=args.dry_run, force=args.force)
    print(report.summary())
    for error in report.errors:
        print(f"  ! {error}", file=sys.stderr)
    return 0 if report.ok else 1


def cmd_probe(config: Config, args: argparse.Namespace) -> int:
    client = RealtingClient(config.realting)
    date_to = datetime.now(timezone.utc)
    date_from = date_to - timedelta(days=args.days)

    payload = client.fetch_raw(date_from, date_to)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    print("=== Сира відповідь Realting ===")
    print(text if args.raw or len(text) <= 4000 else text[:4000] + "\n… (обрізано, повністю — з --raw)")

    rows = extract_rows(payload)
    print(f"\n=== Розпізнано заявок: {len(rows)} ===")
    if rows:
        print("Поля першої заявки:", ", ".join(sorted(rows[0].keys())))

    leads, skipped = normalize_all(rows, load_field_map(config.field_map_file))
    print(f"\n=== Після мапінгу: {len(leads)} придатних, {len(skipped)} пропущено ===")
    for lead in leads[:3]:
        data = lead.to_dict()
        data.pop("raw", None)
        print(json.dumps(data, ensure_ascii=False, indent=2))
    for item in skipped[:3]:
        print(f"пропущено ({item.reason}): {json.dumps(item.raw, ensure_ascii=False)[:300]}")
    if rows and not leads:
        print("\nЖодна заявка не змапилась — додайте власні шляхи у файл REALTING_FIELD_MAP_FILE.")
    return 0


def cmd_check(config: Config, args: argparse.Namespace) -> int:
    ok = True
    print(f"Realting URL:      {config.realting.url}")
    print(f"Авторизація:       {config.realting.auth_mode}")
    print(f"Bitrix24 вебхук:   {config.bitrix.webhook_url.rsplit('/', 1)[0]}/***")
    print(f"Поле зовн. ID:     {config.bitrix.external_id_field}")
    print(f"Файл стану:        {config.state_path}")

    try:
        rows = extract_rows(RealtingClient(config.realting).fetch_raw(
            datetime.now(timezone.utc) - timedelta(days=1), datetime.now(timezone.utc)
        ))
        print(f"Realting:          OK, заявок за добу: {len(rows)}")
    except HttpError as exc:
        ok = False
        print(f"Realting:          ПОМИЛКА — {exc}")

    bitrix = BitrixClient(config.bitrix)
    try:
        fields = bitrix.call("crm.lead.fields") or {}
        print(f"Bitrix24:          OK, полів ліда: {len(fields)}")
        if config.bitrix.external_id_field not in fields:
            ok = False
            print(f"  ! поля {config.bitrix.external_id_field} немає — створіть його в CRM (див. docs/bitrix24-setup.md)")
    except (BitrixError, HttpError) as exc:
        ok = False
        print(f"Bitrix24:          ПОМИЛКА — {exc}")

    print("\nПідсумок:", "все готове до запуску" if ok else "є проблеми, див. вище")
    return 0 if ok else 1


def cmd_stats(config: Config, args: argparse.Namespace) -> int:
    with SyncState(config.state_path) as state:
        last = state.get_last_sync()
        print(f"Файл стану:            {config.state_path}")
        print(f"Синхронізовано заявок: {state.processed_count()}")
        print(f"Останній успішний до:  {last.isoformat() if last else '—'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = Config.from_env(env_file=args.env_file)
    except ConfigError as exc:
        setup_logging(args.log_level or "INFO")
        log.error("помилка конфігурації: %s", exc)
        return 2

    setup_logging(args.log_level or config.log_level)

    handlers = {"sync": cmd_sync, "probe": cmd_probe, "check": cmd_check, "stats": cmd_stats}
    try:
        return handlers[args.command](config, args)
    except (HttpError, BitrixError) as exc:
        log.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        log.warning("перервано користувачем")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
