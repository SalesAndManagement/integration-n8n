"""Оркестрація: вибірка заявок → дедуплікація → створення лідів у Bitrix24."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .bitrix import BitrixClient, BitrixError, duplicate_comment
from .config import Config
from .normalize import Lead, SkippedOrder, load_field_map, normalize_all
from .realting import RealtingClient
from .state import SyncState

log = logging.getLogger(__name__)

CREATED = "created"
DUPLICATE = "duplicate"
ALREADY_IN_CRM = "already_in_crm"
SKIPPED = "skipped"
FAILED = "failed"


@dataclass
class SyncReport:
    date_from: datetime
    date_to: datetime
    fetched: int = 0
    created: int = 0
    duplicates: int = 0
    already_in_crm: int = 0
    skipped: int = 0
    failed: int = 0
    dry_run: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.failed == 0

    def summary(self) -> str:
        window = f"{self.date_from:%Y-%m-%d %H:%M} → {self.date_to:%Y-%m-%d %H:%M} UTC"
        prefix = "DRY-RUN " if self.dry_run else ""
        return (
            f"{prefix}{window}: отримано {self.fetched}, створено {self.created}, "
            f"дублів {self.duplicates}, вже в CRM {self.already_in_crm}, "
            f"пропущено {self.skipped}, помилок {self.failed}"
        )


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
    ) -> SyncReport:
        date_from, date_to = self.window(since, until)
        report = SyncReport(date_from=date_from, date_to=date_to, dry_run=dry_run)

        rows = self.realting.fetch_orders(date_from, date_to)
        report.fetched = len(rows)
        leads, skipped = normalize_all(rows, self._field_map)
        report.skipped = len(skipped)
        for item in skipped:
            log.info("пропущено заявку (%s): %s", item.reason, _short(item.raw))

        for lead in leads:
            try:
                outcome = self.process(lead, dry_run=dry_run, force=force)
            except BitrixError as exc:
                report.failed += 1
                report.errors.append(f"{lead.external_id}: {exc}")
                log.error("заявка %s не імпортована: %s", lead.external_id, exc)
                continue

            if outcome == CREATED:
                report.created += 1
            elif outcome == DUPLICATE:
                report.duplicates += 1
            elif outcome == ALREADY_IN_CRM:
                report.already_in_crm += 1

        # Вікно зсуваємо лише після повністю успішного прогону, щоб не загубити
        # заявки, які впали на помилці Bitrix24 — наступний запуск спробує ще раз.
        if not dry_run and report.ok:
            self.state.set_last_sync(date_to)

        return report

    def process(self, lead: Lead, dry_run: bool = False, force: bool = False) -> str:
        if not force and self.state.is_processed(lead.external_id):
            log.debug("заявка %s уже синхронізована — пропускаю", lead.external_id)
            return ALREADY_IN_CRM

        if dry_run:
            log.info("DRY-RUN: створив би лід %s", self.bitrix.lead_fields(lead))
            return CREATED

        existing = self.bitrix.find_lead_by_external_id(lead.external_id)
        if existing:
            log.info("заявка %s уже є в CRM (лід %s)", lead.external_id, existing)
            self.state.mark_processed(lead.external_id, ALREADY_IN_CRM, existing)
            return ALREADY_IN_CRM

        duplicate_id = self.bitrix.find_duplicate(lead) if self.config.bitrix.comment_on_duplicate else None
        if duplicate_id:
            self.bitrix.add_timeline_comment(duplicate_id, duplicate_comment(lead))
            log.info("заявка %s — дубль контакту, коментар у лід %s", lead.external_id, duplicate_id)
            self.state.mark_processed(lead.external_id, DUPLICATE, duplicate_id)
            return DUPLICATE

        lead_id = self.bitrix.add_lead(lead)
        log.info("заявка %s → створено лід %s", lead.external_id, lead_id)
        self.state.mark_processed(lead.external_id, CREATED, lead_id)
        return CREATED


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
