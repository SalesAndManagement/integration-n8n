import unittest
from datetime import datetime, timedelta, timezone

from helpers import make_config, memory_state
from realting_sync.bitrix import BitrixError
from realting_sync.normalize import Lead
from realting_sync.sync import ALREADY_IN_CRM, CREATED, DUPLICATE, Synchronizer


class FakeRealting:
    def __init__(self, rows):
        self.rows = rows
        self.windows = []

    def fetch_orders(self, date_from, date_to):
        self.windows.append((date_from, date_to))
        return self.rows


class FakeBitrix:
    def __init__(self, existing=None, duplicates=None, fail_on=()):
        self.existing = existing or {}        # external_id -> lead_id
        self.duplicates = duplicates or {}    # phone -> lead_id
        self.fail_on = set(fail_on)           # external_id, на яких кидати помилку
        self.added = []
        self.comments = []

    def find_lead_by_external_id(self, external_id):
        return self.existing.get(external_id)

    def find_duplicate(self, lead):
        return self.duplicates.get(lead.phone)

    def add_lead(self, lead):
        if lead.external_id in self.fail_on:
            raise BitrixError("crm.lead.add", "INVALID_FIELD", "тестова помилка")
        self.added.append(lead)
        lead_id = f"lead-{lead.external_id}"
        self.existing[lead.external_id] = lead_id      # портал знайде його наступного разу
        return lead_id

    def add_timeline_comment(self, lead_id, text):
        self.comments.append((lead_id, text))
        return "comment-1"

    def lead_fields(self, lead):
        return {"TITLE": f"Realting #{lead.external_id}"}


def build(rows, state=None, bitrix=None, config=None):
    state = state or memory_state()
    bitrix = bitrix or FakeBitrix()
    config = config or make_config()
    return Synchronizer(config, FakeRealting(rows), bitrix, state), state, bitrix


class WindowTest(unittest.TestCase):
    def test_first_run_uses_first_run_days(self):
        syncer, state, _ = build([])
        now = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)
        date_from, date_to = syncer.window(until=now)
        self.assertEqual(date_to, now)
        self.assertEqual(date_from, now - timedelta(days=7))

    def test_next_run_uses_last_sync_with_overlap(self):
        syncer, state, _ = build([])
        last = datetime(2026, 9, 14, 11, 0, tzinfo=timezone.utc)
        state.set_last_sync(last)
        now = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)
        date_from, _ = syncer.window(until=now)
        self.assertEqual(date_from, last - timedelta(minutes=15))

    def test_explicit_since_wins(self):
        syncer, state, _ = build([])
        state.set_last_sync(datetime(2026, 9, 14, 11, 0, tzinfo=timezone.utc))
        since = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.assertEqual(syncer.window(since=since)[0], since)


class RunTest(unittest.TestCase):
    def test_creates_leads_and_records_state(self):
        rows = [
            {"id": 1, "name": "Іван Петренко", "phone": "+380671234567"},
            {"id": 2, "name": "Ольга", "email": "olga@example.com"},
        ]
        syncer, state, bitrix = build(rows)
        report = syncer.run()
        self.assertEqual((report.created, report.duplicates, report.failed), (2, 0, 0))
        self.assertEqual(len(bitrix.added), 2)
        self.assertTrue(state.is_processed("1"))
        self.assertIsNotNone(state.get_last_sync())

    def test_second_run_is_idempotent(self):
        rows = [{"id": 1, "phone": "+380671234567"}]
        syncer, state, bitrix = build(rows)
        syncer.run()
        report = syncer.run()
        self.assertEqual(report.created, 0)
        self.assertEqual(report.already_in_crm, 1)
        self.assertEqual(len(bitrix.added), 1)

    def test_existing_external_id_in_crm_is_not_duplicated(self):
        bitrix = FakeBitrix(existing={"1": "900"})
        syncer, state, bitrix = build([{"id": 1, "phone": "+380671234567"}], bitrix=bitrix)
        report = syncer.run()
        self.assertEqual(report.already_in_crm, 1)
        self.assertEqual(bitrix.added, [])
        self.assertTrue(state.is_processed("1"))

    def test_duplicate_contact_gets_timeline_comment(self):
        bitrix = FakeBitrix(duplicates={"+380671234567": "555"})
        syncer, state, bitrix = build([{"id": 9, "phone": "+380671234567", "message": "ще раз"}], bitrix=bitrix)
        report = syncer.run()
        self.assertEqual((report.created, report.duplicates), (0, 1))
        self.assertEqual(bitrix.comments[0][0], "555")
        self.assertIn("#9", bitrix.comments[0][1])

    def test_duplicate_check_can_be_disabled(self):
        config = make_config()
        bitrix_cfg = type(config.bitrix)(**{**config.bitrix.__dict__, "comment_on_duplicate": False})
        config = make_config(bitrix=bitrix_cfg)
        bitrix = FakeBitrix(duplicates={"+380671234567": "555"})
        syncer, _, bitrix = build([{"id": 9, "phone": "+380671234567"}], bitrix=bitrix, config=config)
        report = syncer.run()
        self.assertEqual(report.created, 1)
        self.assertEqual(bitrix.comments, [])

    def test_orders_without_contacts_are_counted_as_skipped(self):
        syncer, _, bitrix = build([{"id": 1}, {"id": 2, "phone": "+380671234567"}])
        report = syncer.run()
        self.assertEqual((report.fetched, report.skipped, report.created), (2, 1, 1))

    def test_failed_order_does_not_advance_window(self):
        bitrix = FakeBitrix(fail_on={"1"})
        syncer, state, bitrix = build([{"id": 1, "phone": "+380671234567"}], bitrix=bitrix)
        report = syncer.run()
        self.assertEqual(report.failed, 1)
        self.assertFalse(report.ok)
        self.assertIsNone(state.get_last_sync())   # наступний запуск спробує ще раз
        self.assertFalse(state.is_processed("1"))

    def test_partial_failure_keeps_successful_leads(self):
        rows = [{"id": 1, "phone": "+380671234567"}, {"id": 2, "phone": "+380509998877"}]
        syncer, state, bitrix = build(rows, bitrix=FakeBitrix(fail_on={"1"}))
        report = syncer.run()
        self.assertEqual((report.created, report.failed), (1, 1))
        self.assertTrue(state.is_processed("2"))
        self.assertFalse(state.is_processed("1"))

    def test_dry_run_writes_nothing(self):
        syncer, state, bitrix = build([{"id": 1, "phone": "+380671234567"}])
        report = syncer.run(dry_run=True)
        self.assertEqual(report.created, 1)
        self.assertTrue(report.dry_run)
        self.assertEqual(bitrix.added, [])
        self.assertFalse(state.is_processed("1"))
        self.assertIsNone(state.get_last_sync())

    def test_force_reprocesses_known_order(self):
        rows = [{"id": 1, "phone": "+380671234567"}]
        syncer, state, bitrix = build(rows)
        syncer.run()
        report = syncer.run(force=True)
        self.assertEqual(report.already_in_crm, 1)   # захист на боці Bitrix24 лишається
        self.assertEqual(len(bitrix.added), 1)

    def test_summary_is_human_readable(self):
        syncer, _, _ = build([{"id": 1, "phone": "+380671234567"}])
        summary = syncer.run().summary()
        self.assertIn("створено 1", summary)
        self.assertIn("UTC", summary)


if __name__ == "__main__":
    unittest.main()
