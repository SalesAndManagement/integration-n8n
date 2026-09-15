"""Оркестрація: вибірка заявок → дедуплікація → створення лідів у Bitrix24."""

from __future__ import annotations

import logging
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .bitrix import BitrixClient, BitrixError, duplicate_comment
from .config import Config
from .normalize import Lead, SkippedOrder, extract_rows, load_field_map, normalize_all
from .realting import RealtingClient
from .state import SyncState

log = logging.getLogger(__name__)

CREATED = "created"
MASKED = "masked"
UPDATED = "updated"
BASELINE = "baseline"
DUPLICATE = "duplicate"
ALREADY_IN_CRM = "already_in_crm"
SKIPPED = "skipped"
FAILED = "failed"


@dataclass
class DrainReport:
    """Підсумок обробки черги вхідних хуків."""

    taken: int = 0
    created: int = 0
    duplicates: int = 0
    already_in_crm: int = 0
    updated: int = 0
    skipped: int = 0
    retried: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.failed == 0 and self.retried == 0

    def summary(self) -> str:
        return (
            f"черга: взято {self.taken}, створено {self.created}, дублів {self.duplicates}, "
            f"вже в CRM {self.already_in_crm}, дозаповнено {self.updated}, пропущено {self.skipped}, "
            f"відкладено {self.retried}, провалено {self.failed}"
        )


def parse_moment(value: str) -> datetime | None:
    """Дата заявки Realting ('2026-09-01 00:15:15') → aware UTC. None, якщо не розпізнали."""
    text = (value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass
class SyncReport:
    date_from: datetime
    date_to: datetime
    fetched: int = 0
    created: int = 0
    duplicates: int = 0
    already_in_crm: int = 0
    updated: int = 0
    skipped: int = 0
    out_of_window: int = 0
    failed: int = 0
    dry_run: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.failed == 0

    def summary(self) -> str:
        window = f"{self.date_from:%Y-%m-%d %H:%M} → {self.date_to:%Y-%m-%d %H:%M} UTC"
        prefix = "DRY-RUN " if self.dry_run else ""
        parts = [
            f"{prefix}{window}: отримано {self.fetched}",
            f"створено {self.created}",
            f"дублів {self.duplicates}",
            f"вже в CRM {self.already_in_crm}",
            f"дозаповнено {self.updated}",
            f"пропущено {self.skipped}",
        ]
        if self.out_of_window:
            parts.append(f"поза вікном {self.out_of_window}")
        parts.append(f"помилок {self.failed}")
        return ", ".join(parts)


class Synchronizer:
    def __init__(
        self,
        config: Config,
        realting: RealtingClient,
        bitrix: BitrixClient,
        state: SyncState,
    ) -> None:
        self.config = config
        self.realting = realting
        self.bitrix = bitrix
        self.state = state
        self._field_map = load_field_map(config.field_map_file)

    def window(self, since: datetime | None = None, until: datetime | None = None) -> tuple[datetime, datetime]:
        """Період вибірки: від останнього успішного запуску мінус нахлест."""
        now = until or datetime.now(timezone.utc)
        if since:
            return since, now
        last = self.state.get_last_sync()
        if last:
            return last - timedelta(minutes=self.config.overlap_minutes), now
        return now - timedelta(days=self.config.first_run_days), now

    def run(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
        dry_run: bool = False,
        force: bool = False,
        whole_archive: bool | None = None,
    ) -> SyncReport:
        date_from, date_to = self.window(since, until)
        report = SyncReport(date_from=date_from, date_to=date_to, dry_run=dry_run)

        rows = self.realting.fetch_orders(date_from, date_to)
        report.fetched = len(rows)
        leads, skipped = normalize_all(rows, self._field_map, self.config.skip_masked)
        report.skipped = len(skipped)
        for item in skipped:
            log.info("пропущено заявку (%s): %s", item.reason, _short(item.raw))

        # Експорт Realting не звужується параметрами дат — він щоразу віддає весь
        # архів заявок. Тому вікно застосовуємо самі, інакше перший же прогін
        # завантажив би в CRM усі історичні заявки.
        if not (self.config.whole_archive if whole_archive is None else whole_archive):
            within: list[Lead] = []
            for lead in leads:
                created = parse_moment(lead.created_at)
                if created is not None and created < date_from:
                    report.out_of_window += 1
                    continue
                within.append(lead)
            leads = within

        for lead in leads:
            try:
                outcome = self.process(lead, dry_run=dry_run, force=force)
            except BitrixError as exc:
                report.failed += 1
                report.errors.append(f"{lead.external_id}: {exc}")
                log.error("заявка %s не імпортована: %s", lead.external_id, exc)
                continue

            if outcome in (CREATED, MASKED):
                report.created += 1
            elif outcome == UPDATED:
                report.updated += 1
            elif outcome == DUPLICATE:
                report.duplicates += 1
            elif outcome == ALREADY_IN_CRM:
                report.already_in_crm += 1

        # Вікно зсуваємо лише після повністю успішного прогону, щоб не загубити
        # заявки, які впали на помилці Bitrix24 — наступний запуск спробує ще раз.
        if not dry_run and report.ok:
            self.state.set_last_sync(date_to)

        return report

    def seed(self) -> int:
        """Позначає наявні заявки з відкритими контактами як уже оброблені.

        Потрібно на старті: історію в CRM не вантажимо, але й не хочемо, щоб вона
        поїхала туди при першому ж прогоні. Заявки із замаскованими контактами
        свідомо НЕ позначаємо — коли Realting їх відкриє, вони приїдуть як нові.
        """
        rows = self.realting.fetch_orders(
            datetime.now(timezone.utc) - timedelta(days=3650), datetime.now(timezone.utc)
        )
        # Беремо і замасковані теж, але позначаємо їх інакше: як MASKED, а не BASELINE.
        # Завдяки цьому заявка, якій Realting відкриє контакти, все одно приїде в CRM,
        # тоді як історія з уже відкритими контактами лишиться позаду назавжди.
        leads, _ = normalize_all(rows, self._field_map, skip_masked=False)
        marked = 0
        for lead in leads:
            if self.state.is_processed(lead.external_id):
                continue
            self.state.mark_processed(lead.external_id, MASKED if lead.masked else BASELINE, None)
            marked += 1
        self.state.set_last_sync(datetime.now(timezone.utc))
        return marked

    def drain(self, limit: int = 50, now: datetime | None = None) -> DrainReport:
        """Обробляє чергу вхідних хуків: кожен запис → ліди в Bitrix24.

        Помилка порталу не втрачає заявку: запис лишається в черзі й отримує
        наступну спробу за наростаючою паузою (1 хв → 6 год).
        """
        report = DrainReport()
        moment = now or datetime.now(timezone.utc)

        with self.state.lock:
            rows = self.state.due_inbox(limit=limit, now=moment)
        report.taken = len(rows)

        for row in rows:
            attempts = int(row["attempts"]) + 1
            try:
                payload = json.loads(row["payload"])
            except json.JSONDecodeError as exc:
                with self.state.lock:
                    self.state.mark_inbox_done(int(row["id"]), None, status="skipped")
                report.skipped += 1
                log.error("запис черги #%s не є JSON (%s) — пропускаю", row["id"], exc)
                continue

            leads, skipped = normalize_all(extract_rows(payload), self._field_map, self.config.skip_masked)
            if skipped:
                for item in skipped:
                    log.info("хук #%s: пропущено заявку (%s): %s", row["id"], item.reason, _short(item.raw))
            if not leads:
                with self.state.lock:
                    self.state.mark_inbox_done(int(row["id"]), None, status="skipped")
                report.skipped += 1
                continue

            try:
                outcomes = [self.process(lead) for lead in leads]
            except BitrixError as exc:
                with self.state.lock:
                    next_at = self.state.mark_inbox_retry(int(row["id"]), str(exc), attempts, now=moment)
                if next_at:
                    report.retried += 1
                    log.warning("хук #%s: спроба %s невдала (%s), наступна о %s", row["id"], attempts, exc, next_at)
                else:
                    report.failed += 1
                    report.errors.append(f"#{row['id']}: {exc}")
                    log.error("хук #%s: вичерпано спроби — %s", row["id"], exc)
                continue

            report.created += outcomes.count(CREATED) + outcomes.count(MASKED)
            report.updated += outcomes.count(UPDATED)
            report.duplicates += outcomes.count(DUPLICATE)
            report.already_in_crm += outcomes.count(ALREADY_IN_CRM)
            with self.state.lock:
                self.state.mark_inbox_done(int(row["id"]), leads[0].external_id)

        return report

    def process(self, lead: Lead, dry_run: bool = False, force: bool = False) -> str:
        known = None if force else self.state.get_outcome(lead.external_id)
        if known:
            # Заявку, заведену із закритими контактами, чекаємо дозаповнити —
            # решту пропускаємо без жодного звернення до порталу.
            needs_topup = known[0] == MASKED and not lead.masked
            if not needs_topup:
                log.debug("заявка %s уже синхронізована — пропускаю", lead.external_id)
                return ALREADY_IN_CRM

        if dry_run:
            action = "дозаповнив би" if known else "створив би"
            log.info("DRY-RUN: %s лід %s", action, self.bitrix.lead_fields(lead))
            return UPDATED if known else CREATED

        existing = self.bitrix.find_lead(lead.external_id)
        if existing:
            lead_id = str(existing.get("ID"))
            has_contacts = existing.get("HAS_PHONE") == "Y" or existing.get("HAS_EMAIL") == "Y"
            if not lead.masked and not has_contacts and (lead.phone or lead.email):
                # Realting відкрив контакти — підставляємо їх у наявний лід
                self.bitrix.update_lead(lead_id, self.bitrix.contact_fields(lead))
                log.info("заявка %s → контакти відкрились, дозаповнено лід %s", lead.external_id, lead_id)
                self.state.mark_processed(lead.external_id, UPDATED, lead_id)
                return UPDATED
            log.info("заявка %s уже є в CRM (лід %s)", lead.external_id, lead_id)
            self.state.mark_processed(lead.external_id, ALREADY_IN_CRM, lead_id)
            return ALREADY_IN_CRM

        duplicate_id = None
        if self.config.bitrix.comment_on_duplicate and not lead.masked:
            duplicate_id = self.bitrix.find_duplicate(lead)
        if duplicate_id:
            self.bitrix.add_timeline_comment(duplicate_id, duplicate_comment(lead))
            log.info("заявка %s — дубль контакту, коментар у лід %s", lead.external_id, duplicate_id)
            self.state.mark_processed(lead.external_id, DUPLICATE, duplicate_id)
            return DUPLICATE

        lead_id = self.bitrix.add_lead(lead)
        outcome = MASKED if lead.masked else CREATED
        log.info(
            "заявка %s → створено лід %s%s",
            lead.external_id, lead_id, " (контакти ще закриті)" if lead.masked else "",
        )
        self.state.mark_processed(lead.external_id, outcome, lead_id)
        return outcome


def _short(value: object, limit: int = 300) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[:limit] + "…"


def build(config: Config, state: SyncState) -> Synchronizer:
    return Synchronizer(
        config=config,
        realting=RealtingClient(config.realting),
        bitrix=BitrixClient(config.bitrix),
        state=state,
    )
