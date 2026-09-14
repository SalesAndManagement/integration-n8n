"""Стан синхронізації у SQLite: що вже імпортовано і до якого моменту."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS processed_orders (
    external_id   TEXT PRIMARY KEY,
    bitrix_lead_id TEXT,
    outcome       TEXT NOT NULL,
    synced_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_processed_synced_at ON processed_orders(synced_at);
"""


class SyncState:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SyncState":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # --- оброблені заявки ---------------------------------------------

    def is_processed(self, external_id: str) -> bool:
        with closing(self._conn.execute(
            "SELECT 1 FROM processed_orders WHERE external_id = ?", (external_id,)
        )) as cur:
            return cur.fetchone() is not None

    def mark_processed(self, external_id: str, outcome: str, bitrix_lead_id: str | None = None) -> None:
        self._conn.execute(
            "INSERT INTO processed_orders (external_id, bitrix_lead_id, outcome, synced_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(external_id) DO UPDATE SET "
            "  bitrix_lead_id = excluded.bitrix_lead_id, outcome = excluded.outcome, synced_at = excluded.synced_at",
            (external_id, bitrix_lead_id, outcome, datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        self._conn.commit()

    def processed_count(self) -> int:
        with closing(self._conn.execute("SELECT COUNT(*) AS n FROM processed_orders")) as cur:
            return int(cur.fetchone()["n"])

    # --- службові дані --------------------------------------------------

    def get_meta(self, key: str) -> str | None:
        with closing(self._conn.execute("SELECT value FROM sync_meta WHERE key = ?", (key,))) as cur:
            row = cur.fetchone()
            return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO sync_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._conn.commit()

    def get_last_sync(self) -> datetime | None:
        raw = self.get_meta("last_sync_at")
        if not raw:
            return None
        parsed = datetime.fromisoformat(raw)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    def set_last_sync(self, moment: datetime) -> None:
        self.set_meta("last_sync_at", moment.astimezone(timezone.utc).isoformat(timespec="seconds"))
