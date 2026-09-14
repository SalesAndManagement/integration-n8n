"""Стан синхронізації у SQLite: що вже імпортовано і до якого моменту."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timedelta, timezone
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

-- Черга вхідних хуків: приймач пише сюди й одразу відповідає 200,
-- а воркер уже спокійно несе заявку в Bitrix24 (з ретраями).
CREATE TABLE IF NOT EXISTS inbox (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at     TEXT NOT NULL,
    source          TEXT NOT NULL DEFAULT 'webhook',
    payload         TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',   -- pending | done | failed | skipped
    attempts        INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    last_error      TEXT,
    external_id     TEXT,
    processed_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_inbox_pending ON inbox(status, next_attempt_at);
"""

# Паузи між спробами доставки в Bitrix24: 1 хв, 5 хв, 15 хв, 1 год, 6 год.
RETRY_BACKOFF_SECONDS = (60, 300, 900, 3600, 21600)
MAX_ATTEMPTS = len(RETRY_BACKOFF_SECONDS) + 1


class SyncState:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        # приймач хуків пише з потоків HTTP-сервера, воркер читає зі свого —
        # тому зʼєднання спільне, а доступ серіалізується self.lock
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.lock = threading.RLock()
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

    # --- черга вхідних хуків ----------------------------------------

    def enqueue(self, payload: str, source: str = "webhook") -> int:
        cur = self._conn.execute(
            "INSERT INTO inbox (received_at, source, payload, status, next_attempt_at) "
            "VALUES (?, ?, ?, 'pending', NULL)",
            (_now_iso(), source, payload),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def due_inbox(self, limit: int = 50, now: datetime | None = None) -> list[sqlite3.Row]:
        moment = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
        with closing(self._conn.execute(
            "SELECT * FROM inbox WHERE status = 'pending' AND (next_attempt_at IS NULL OR next_attempt_at <= ?) "
            "ORDER BY id LIMIT ?",
            (moment, limit),
        )) as cur:
            return cur.fetchall()

    def mark_inbox_done(self, inbox_id: int, external_id: str | None, status: str = "done") -> None:
        self._conn.execute(
            "UPDATE inbox SET status = ?, external_id = ?, processed_at = ?, last_error = NULL WHERE id = ?",
            (status, external_id, _now_iso(), inbox_id),
        )
        self._conn.commit()

    def mark_inbox_retry(self, inbox_id: int, error: str, attempts: int, now: datetime | None = None) -> datetime | None:
        """Планує наступну спробу; після вичерпання лімітів позначає 'failed'."""
        moment = now or datetime.now(timezone.utc)
        if attempts >= MAX_ATTEMPTS:
            self._conn.execute(
                "UPDATE inbox SET status = 'failed', attempts = ?, last_error = ?, processed_at = ? WHERE id = ?",
                (attempts, error[:1000], _now_iso(), inbox_id),
            )
            self._conn.commit()
            return None

        delay = RETRY_BACKOFF_SECONDS[min(attempts, len(RETRY_BACKOFF_SECONDS)) - 1]
        next_at = moment + timedelta(seconds=delay)
        self._conn.execute(
            "UPDATE inbox SET attempts = ?, last_error = ?, next_attempt_at = ? WHERE id = ?",
            (attempts, error[:1000], next_at.isoformat(timespec="seconds"), inbox_id),
        )
        self._conn.commit()
        return next_at

    def requeue_failed(self) -> int:
        cur = self._conn.execute(
            "UPDATE inbox SET status = 'pending', attempts = 0, next_attempt_at = NULL, last_error = NULL "
            "WHERE status = 'failed'"
        )
        self._conn.commit()
        return int(cur.rowcount)

    def inbox_stats(self) -> dict[str, int]:
        with closing(self._conn.execute("SELECT status, COUNT(*) AS n FROM inbox GROUP BY status")) as cur:
            return {row["status"]: int(row["n"]) for row in cur.fetchall()}

    def last_inbox_errors(self, limit: int = 5) -> list[sqlite3.Row]:
        with closing(self._conn.execute(
            "SELECT id, external_id, attempts, last_error, received_at FROM inbox "
            "WHERE last_error IS NOT NULL ORDER BY id DESC LIMIT ?",
            (limit,),
        )) as cur:
            return cur.fetchall()

    # --- службові дані --------------------------------------------------

    def get_last_sync(self) -> datetime | None:
        raw = self.get_meta("last_sync_at")
        if not raw:
            return None
        parsed = datetime.fromisoformat(raw)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    def set_last_sync(self, moment: datetime) -> None:
        self.set_meta("last_sync_at", moment.astimezone(timezone.utc).isoformat(timespec="seconds"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
