import unittest
from datetime import datetime, timezone

from realting_sync.state import SyncState


class StateTest(unittest.TestCase):
    def setUp(self):
        self.state = SyncState(":memory:")

    def tearDown(self):
        self.state.close()

    def test_unknown_order_is_not_processed(self):
        self.assertFalse(self.state.is_processed("nope"))

    def test_mark_and_check(self):
        self.state.mark_processed("1", "created", "lead-1")
        self.assertTrue(self.state.is_processed("1"))
        self.assertEqual(self.state.processed_count(), 1)

    def test_mark_is_idempotent(self):
        self.state.mark_processed("1", "created", "lead-1")
        self.state.mark_processed("1", "duplicate", "lead-2")
        self.assertEqual(self.state.processed_count(), 1)

    def test_last_sync_roundtrip_is_utc_aware(self):
        moment = datetime(2026, 9, 14, 12, 30, tzinfo=timezone.utc)
        self.state.set_last_sync(moment)
        restored = self.state.get_last_sync()
        self.assertEqual(restored, moment)
        self.assertIsNotNone(restored.tzinfo)

    def test_last_sync_empty_by_default(self):
        self.assertIsNone(self.state.get_last_sync())

    def test_state_survives_reopen(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "state.db"
            with SyncState(path) as state:
                state.mark_processed("7", "created", "lead-7")
            with SyncState(path) as state:
                self.assertTrue(state.is_processed("7"))


if __name__ == "__main__":
    unittest.main()
