"""Воркер черги: доставка, ретраї з паузами, відсутність втрат."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone

from helpers import make_config, memory_state
from realting_sync.bitrix import BitrixError
from realting_sync.state import MAX_ATTEMPTS, SyncState
from realting_sync.sync import Synchronizer
from test_sync import FakeBitrix, FakeRealting

ORDER = {"id": 501, "name": "Іван Петренко", "phone": "+380671234567", "message": "Цікавить обʼєкт"}
NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


def build(bitrix=None, state=None):
    state = state or memory_state()
    bitrix = bitrix or FakeBitrix()
    return Synchronizer(make_config(), FakeRealting([]), bitrix, state), state, bitrix


class DrainTest(unittest.TestCase):
    def test_queued_hook_becomes_a_lead(self):
        syncer, state, bitrix = build()
        state.enqueue(json.dumps(ORDER))
        report = syncer.drain()
        self.assertEqual((report.taken, report.created), (1, 1))
        self.assertEqual(len(bitrix.added), 1)
        self.assertEqual(bitrix.added[0].external_id, "501")
        self.assertEqual(state.inbox_stats(), {"done": 1})

    def test_hook_with_wrapper_and_several_orders(self):
        syncer, state, bitrix = build()
        state.enqueue(json.dumps({"data": [ORDER, {**ORDER, "id": 502, "phone": "+380509998877"}]}))
        report = syncer.drain()
        self.assertEqual(report.created, 2)
        self.assertEqual(state.inbox_stats(), {"done": 1})

    def test_duplicate_delivery_creates_one_lead(self):
        syncer, state, bitrix = build()
        state.enqueue(json.dumps(ORDER))
        state.enqueue(json.dumps(ORDER))          # Realting повторив доставку
        report = syncer.drain()
        self.assertEqual((report.created, report.already_in_crm), (1, 1))
        self.assertEqual(len(bitrix.added), 1)

    def test_order_without_contacts_is_skipped_not_retried(self):
        syncer, state, bitrix = build()
        state.enqueue(json.dumps({"id": 777, "name": "Без контактів"}))
        report = syncer.drain()
        self.assertEqual((report.skipped, report.created), (1, 0))
        self.assertEqual(state.inbox_stats(), {"skipped": 1})

    def test_broken_json_in_queue_is_skipped(self):
        syncer, state, _ = build()
        state._conn.execute(
            "INSERT INTO inbox (received_at, source, payload, status) VALUES (?, 'webhook', ?, 'pending')",
            (NOW.isoformat(), "{зламано"),
        )
        state._conn.commit()
        report = syncer.drain()
        self.assertEqual(report.skipped, 1)
        self.assertEqual(state.inbox_stats(), {"skipped": 1})

    def test_bitrix_failure_keeps_order_in_queue(self):
        syncer, state, bitrix = build(bitrix=FakeBitrix(fail_on={"501"}))
        state.enqueue(json.dumps(ORDER))
        report = syncer.drain(now=NOW)
        self.assertEqual((report.retried, report.created), (1, 0))
        self.assertEqual(state.inbox_stats(), {"pending": 1})       # заявка не втрачена
        self.assertEqual(state.due_inbox(now=NOW), [])              # але чекає паузу
        self.assertEqual(len(state.due_inbox(now=NOW + timedelta(minutes=2))), 1)

    def test_retry_succeeds_after_portal_recovers(self):
        bitrix = FakeBitrix(fail_on={"501"})
        syncer, state, bitrix = build(bitrix=bitrix)
        state.enqueue(json.dumps(ORDER))
        syncer.drain(now=NOW)
        bitrix.fail_on.clear()                                       # портал ожив
        report = syncer.drain(now=NOW + timedelta(minutes=2))
        self.assertEqual(report.created, 1)
        self.assertEqual(state.inbox_stats(), {"done": 1})

    def test_backoff_grows_between_attempts(self):
        syncer, state, _ = build(bitrix=FakeBitrix(fail_on={"501"}))
        state.enqueue(json.dumps(ORDER))
        moment = NOW
        delays = []
        for _ in range(3):
            syncer.drain(now=moment)
            row = state._conn.execute("SELECT next_attempt_at FROM inbox WHERE id = 1").fetchone()
            next_at = datetime.fromisoformat(row["next_attempt_at"])
            delays.append((next_at - moment).total_seconds())
            moment = next_at
        self.assertEqual(delays, sorted(delays))
        self.assertGreater(delays[-1], delays[0])

    def test_exhausted_attempts_are_marked_failed(self):
        syncer, state, _ = build(bitrix=FakeBitrix(fail_on={"501"}))
        state.enqueue(json.dumps(ORDER))
        moment = NOW
        for _ in range(MAX_ATTEMPTS):
            syncer.drain(now=moment)
            moment += timedelta(hours=12)
        self.assertEqual(state.inbox_stats(), {"failed": 1})
        errors = state.last_inbox_errors()
        self.assertIn("тестова помилка", errors[0]["last_error"])

    def test_failed_can_be_requeued_manually(self):
        syncer, state, bitrix = build(bitrix=FakeBitrix(fail_on={"501"}))
        state.enqueue(json.dumps(ORDER))
        moment = NOW
        for _ in range(MAX_ATTEMPTS):
            syncer.drain(now=moment)
            moment += timedelta(hours=12)
        bitrix.fail_on.clear()
        self.assertEqual(state.requeue_failed(), 1)
        report = syncer.drain(now=moment)
        self.assertEqual(report.created, 1)
        self.assertEqual(state.inbox_stats(), {"done": 1})

    def test_limit_is_respected(self):
        syncer, state, _ = build()
        for n in range(5):
            state.enqueue(json.dumps({**ORDER, "id": 600 + n}))
        report = syncer.drain(limit=2)
        self.assertEqual(report.taken, 2)
        self.assertEqual(state.inbox_stats(), {"done": 2, "pending": 3})

    def test_empty_queue_is_a_noop(self):
        syncer, _, bitrix = build()
        report = syncer.drain()
        self.assertEqual(report.taken, 0)
        self.assertTrue(report.ok)
        self.assertEqual(bitrix.added, [])


if __name__ == "__main__":
    unittest.main()
