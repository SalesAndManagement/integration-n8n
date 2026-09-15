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
        self.updated = []

    def find_lead(self, external_id):
        row = self.existing.get(external_id)
        if row is None:
            return None
        if isinstance(row, dict):
            return row
        return {"ID": row, "HAS_PHONE": "N", "HAS_EMAIL": "N"}

    def find_lead_by_external_id(self, external_id):
        row = self.find_lead(external_id)
        return None if row is None else str(row["ID"])

    def update_lead(self, lead_id, fields):
        self.updated.append((lead_id, fields))
        return True

    def contact_fields(self, lead):
        return {"PHONE": lead.phone, "EMAIL": lead.email, "NAME": lead.first_name}

    def find_duplicate(self, lead):
        return self.duplicates.get(lead.phone)

    def add_lead(self, lead):
        if lead.external_id in self.fail_on:
            raise BitrixError("crm.lead.add", "INVALID_FIELD", "тестова помилка")
        self.added.append(lead)
        lead_id = f"lead-{lead.external_id}"
        self.existing[lead.external_id] = {            # портал знайде його наступного разу
            "ID": lead_id,
            "HAS_PHONE": "Y" if (lead.phone and not lead.masked) else "N",
            "HAS_EMAIL": "Y" if (lead.email and not lead.masked) else "N",
        }
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
        bitrix = FakeBitrix(existing={"1": {"ID": "900", "HAS_PHONE": "Y", "HAS_EMAIL": "N"}})
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


class ClientSideWindowTest(unittest.TestCase):
    """Експорт Realting ігнорує параметри дат — вікно доводиться застосовувати самим."""

    OLD = {"id": 1, "phone": "+380671234567", "created_at": "2025-03-16 01:16:06"}
    NEW = {"id": 2, "phone": "+380509998877", "created_at": "2026-09-14 10:00:00"}
    NO_DATE = {"id": 3, "phone": "+380501112233"}

    def test_old_orders_are_left_outside_the_window(self):
        syncer, state, bitrix = build([self.OLD, self.NEW])
        report = syncer.run(until=datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(report.created, 1)
        self.assertEqual(report.out_of_window, 1)
        self.assertEqual([lead.external_id for lead in bitrix.added], ["2"])

    def test_whole_archive_takes_everything(self):
        syncer, state, bitrix = build([self.OLD, self.NEW])
        report = syncer.run(until=datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc), whole_archive=True)
        self.assertEqual(report.created, 2)
        self.assertEqual(report.out_of_window, 0)

    def test_order_without_a_date_is_not_dropped(self):
        syncer, _, bitrix = build([self.NO_DATE])
        report = syncer.run(until=datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(report.created, 1)

    def test_explicit_since_moves_the_boundary(self):
        syncer, _, bitrix = build([self.OLD, self.NEW])
        report = syncer.run(
            since=datetime(2025, 1, 1, tzinfo=timezone.utc),
            until=datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(report.created, 2)

    def test_summary_mentions_out_of_window_only_when_there_is_something(self):
        syncer, _, _ = build([self.NEW])
        self.assertNotIn("поза вікном", syncer.run(until=datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)).summary())


class ParseMomentTest(unittest.TestCase):
    def test_realting_format(self):
        from realting_sync.sync import parse_moment

        self.assertEqual(
            parse_moment("2026-09-01 00:15:15"),
            datetime(2026, 9, 1, 0, 15, 15, tzinfo=timezone.utc),
        )

    def test_unknown_values_are_none(self):
        from realting_sync.sync import parse_moment

        self.assertIsNone(parse_moment(""))
        self.assertIsNone(parse_moment("вчора"))


class SeedTest(unittest.TestCase):
    """Старт «тільки нові»: історію позначаємо, але в CRM не вантажимо."""

    OPEN_OLD = {"id": 1, "name": "Олена", "phone": "+380671234567", "created_at": "2025-03-16 01:16:06",
                "status_title": "Request accepted to work"}
    MASKED = {"id": 2, "name": "Ник***", "phone": "+790******21", "email": "n***@gmail.com",
              "created_at": "2026-09-01 00:15:15", "status_title": "Request not accepted to work"}

    def test_seed_marks_open_orders_without_touching_crm(self):
        syncer, state, bitrix = build([self.OPEN_OLD, self.MASKED])
        marked = syncer.seed()
        self.assertEqual(marked, 1)
        self.assertEqual(bitrix.added, [])
        self.assertTrue(state.is_processed("1"))

    def test_masked_order_is_not_marked_and_arrives_when_it_opens(self):
        syncer, state, bitrix = build([self.OPEN_OLD, self.MASKED])
        syncer.seed()
        self.assertFalse(state.is_processed("2"))          # замаскована лишається «небаченою»

        # Realting прийняв заявку в роботу: контакти відкрились, дата створення стара
        opened = {**self.MASKED, "name": "Микола", "phone": "+380509998877", "email": "m@example.com",
                  "status_title": "Request accepted to work"}
        syncer.realting.rows = [self.OPEN_OLD, opened]
        report = syncer.run(whole_archive=True)
        self.assertEqual(report.created, 1)
        self.assertEqual([lead.external_id for lead in bitrix.added], ["2"])

    def test_seed_is_idempotent(self):
        syncer, state, _ = build([self.OPEN_OLD])
        self.assertEqual(syncer.seed(), 1)
        self.assertEqual(syncer.seed(), 0)

    def test_after_seed_nothing_historical_reaches_crm(self):
        syncer, state, bitrix = build([self.OPEN_OLD, self.MASKED])
        syncer.seed()
        report = syncer.run(whole_archive=True)
        self.assertEqual(report.created, 0)
        self.assertEqual(report.already_in_crm, 1)
        self.assertEqual(bitrix.added, [])

    def test_brand_new_order_after_seed_is_imported(self):
        syncer, state, bitrix = build([self.OPEN_OLD])
        syncer.seed()
        syncer.realting.rows = [self.OPEN_OLD, {"id": 99, "name": "Нова Заявка", "phone": "+380631112233",
                                                "created_at": "2026-09-14 12:00:00"}]
        report = syncer.run(whole_archive=True)
        self.assertEqual(report.created, 1)
        self.assertEqual(bitrix.added[0].external_id, "99")


class WholeArchiveConfigTest(unittest.TestCase):
    def test_config_flag_switches_off_the_date_window(self):
        config = make_config(whole_archive=True)
        old = {"id": 1, "phone": "+380671234567", "created_at": "2025-01-01 00:00:00"}
        syncer, _, bitrix = build([old], config=config)
        report = syncer.run(until=datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(report.created, 1)
        self.assertEqual(report.out_of_window, 0)

    def test_explicit_flag_overrides_config(self):
        config = make_config(whole_archive=True)
        old = {"id": 1, "phone": "+380671234567", "created_at": "2025-01-01 00:00:00"}
        syncer, _, _ = build([old], config=config)
        report = syncer.run(until=datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc), whole_archive=False)
        self.assertEqual(report.out_of_window, 1)


class MaskedImportTest(unittest.TestCase):
    """Режим IMPORT_MASKED: лід заводиться одразу, контакти підставляються пізніше."""

    MASKED = {"id": 164070, "name": "Гал***", "phone": "+488******68",
              "email": "gal***@varemestate.com", "message": "tes***",
              "status_title": "Request not accepted to work", "created_at": "2026-09-15 15:19:00"}
    OPENED = {"id": 164070, "name": "Галина Ковальчук", "phone": "+48 888 111 268",
              "email": "galina@varemestate.com", "message": "Цікавить квартира",
              "status_title": "Request accepted to work", "created_at": "2026-09-15 15:19:00"}

    def config(self):
        return make_config(import_masked=True, skip_masked=False, whole_archive=True)

    def test_masked_order_creates_a_lead_without_contact_fields(self):
        syncer, state, bitrix = build([self.MASKED], config=self.config())
        report = syncer.run()
        self.assertEqual(report.created, 1)
        self.assertEqual(len(bitrix.added), 1)
        self.assertTrue(bitrix.added[0].masked)
        self.assertEqual(state.get_outcome("164070")[0], "masked")

    def test_contacts_are_filled_in_when_realting_opens_them(self):
        syncer, state, bitrix = build([self.MASKED], config=self.config())
        syncer.run()

        syncer.realting.rows = [self.OPENED]
        report = syncer.run()

        self.assertEqual(report.updated, 1)
        self.assertEqual(report.created, 0)
        self.assertEqual(len(bitrix.added), 1)                  # другого ліда не з'явилось
        lead_id, fields = bitrix.updated[0]
        self.assertEqual(lead_id, "lead-164070")
        self.assertEqual(fields["PHONE"], "+48888111268")
        self.assertEqual(fields["EMAIL"], "galina@varemestate.com")
        self.assertEqual(state.get_outcome("164070")[0], "updated")

    def test_still_masked_order_is_not_touched_again(self):
        syncer, state, bitrix = build([self.MASKED], config=self.config())
        syncer.run()
        report = syncer.run()
        self.assertEqual((report.created, report.updated, report.already_in_crm), (0, 0, 1))
        self.assertEqual(bitrix.updated, [])

    def test_top_up_happens_only_once(self):
        syncer, state, bitrix = build([self.MASKED], config=self.config())
        syncer.run()
        syncer.realting.rows = [self.OPENED]
        syncer.run()
        report = syncer.run()
        self.assertEqual(report.updated, 0)
        self.assertEqual(len(bitrix.updated), 1)

    def test_masked_order_does_not_trigger_duplicate_search(self):
        # шукати дубль за "+488******68" безглуздо і шкідливо
        bitrix = FakeBitrix(duplicates={"+488******68": "555"})
        syncer, _, bitrix = build([self.MASKED], bitrix=bitrix, config=self.config())
        syncer.run()
        self.assertEqual(bitrix.comments, [])
        self.assertEqual(len(bitrix.added), 1)

    def test_default_mode_still_skips_masked_orders(self):
        syncer, state, bitrix = build([self.MASKED])
        report = syncer.run(whole_archive=True)
        self.assertEqual((report.created, report.skipped), (0, 1))
        self.assertEqual(bitrix.added, [])

    def test_lost_state_still_tops_up_by_portal_flags(self):
        # база стану втрачена, але портал каже, що в ліда немає контактів
        bitrix = FakeBitrix(existing={"164070": {"ID": "900", "HAS_PHONE": "N", "HAS_EMAIL": "N"}})
        syncer, state, bitrix = build([self.OPENED], bitrix=bitrix, config=self.config())
        report = syncer.run()
        self.assertEqual(report.updated, 1)
        self.assertEqual(bitrix.updated[0][0], "900")
