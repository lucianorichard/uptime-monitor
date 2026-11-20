"""SQLite storage for check results (standard library sqlite3, no ORM)."""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from .checker import CheckResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS checks (
    id          INTEGER PRIMARY KEY,
    target      TEXT    NOT NULL,
    checked_at  TEXT    NOT NULL,             -- ISO 8601 UTC, sortable as text
    ok          INTEGER NOT NULL CHECK (ok IN (0, 1)),
    status_code INTEGER,                      -- NULL when no response arrived
    response_ms REAL,                         -- NULL when no response arrived
    error       TEXT
);
CREATE INDEX IF NOT EXISTS checks_target_time ON checks (target, checked_at);
"""


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="milliseconds")


class Storage:
    """Thin repository. One connection guarded by a lock: the monitor writes a
    handful of rows per interval, so contention is not a concern."""

    def __init__(self, path: str) -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            if path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")  # readers do not block the writer
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def add(self, results: list[CheckResult]) -> None:
        rows = [
            (r.target, _iso(r.checked_at), int(r.ok), r.status_code, r.response_ms, r.error)
            for r in results
        ]
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT INTO checks (target, checked_at, ok, status_code, response_ms, error) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )

    def recent(self, target: str, limit: int = 50) -> list[sqlite3.Row]:
        """Most recent checks first."""
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM checks WHERE target = ? ORDER BY checked_at DESC, id DESC LIMIT ?",
                (target, limit),
            ).fetchall()

    def since(self, target: str, since: datetime) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT ok, response_ms FROM checks WHERE target = ? AND checked_at >= ?",
                (target, _iso(since)),
            ).fetchall()

    def first_check(self, target: str) -> datetime | None:
        """Time of the oldest stored check: when monitoring of this target began
        (within the retention period)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT MIN(checked_at) FROM checks WHERE target = ?", (target,)
            ).fetchone()
        return datetime.fromisoformat(row[0]) if row and row[0] else None

    def prune(self, before: datetime) -> int:
        with self._lock, self._conn:
            return self._conn.execute(
                "DELETE FROM checks WHERE checked_at < ?", (_iso(before),)
            ).rowcount
