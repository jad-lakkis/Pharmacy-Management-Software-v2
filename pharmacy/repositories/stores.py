"""Storage backends for repositories.

A store persists opaque JSON strings keyed by id, in insertion order.
Repositories handle entity encoding, so the same repository code runs
unchanged over memory (tests) or SQLite (the running app).

Both backends support transactions with rollback: memory stores snapshot
their contents, SQLite uses BEGIN/COMMIT/ROLLBACK.
"""

import sqlite3
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional


class Store(ABC):
    @abstractmethod
    def get(self, key: str) -> Optional[str]: ...

    @abstractmethod
    def put(self, key: str, payload: str) -> None: ...

    @abstractmethod
    def delete(self, key: str) -> bool: ...

    @abstractmethod
    def values(self) -> list[str]: ...

    @abstractmethod
    def last(self) -> Optional[str]: ...

    @abstractmethod
    def count(self) -> int: ...


class MemoryStore(Store):
    def __init__(self) -> None:
        self._rows: dict[str, str] = {}

    def get(self, key: str) -> Optional[str]:
        return self._rows.get(key)

    def put(self, key: str, payload: str) -> None:
        self._rows[key] = payload

    def delete(self, key: str) -> bool:
        return self._rows.pop(key, None) is not None

    def values(self) -> list[str]:
        return list(self._rows.values())

    def last(self) -> Optional[str]:
        if not self._rows:
            return None
        return next(reversed(self._rows.values()))

    def count(self) -> int:
        return len(self._rows)

    # Transaction support
    def snapshot(self) -> dict[str, str]:
        return dict(self._rows)

    def restore(self, snapshot: dict[str, str]) -> None:
        self._rows = dict(snapshot)


class SqliteDatabase:
    """One SQLite connection shared by all SQLite stores of an app."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        if self.path != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")

    def ensure_table(self, table: str) -> None:
        _check_identifier(table)
        self.conn.execute(
            f'CREATE TABLE IF NOT EXISTS "{table}" '
            "(seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE, data TEXT NOT NULL)"
        )

    def begin(self) -> None:
        self.conn.execute("BEGIN IMMEDIATE")

    def commit(self) -> None:
        self.conn.execute("COMMIT")

    def rollback(self) -> None:
        self.conn.execute("ROLLBACK")

    def close(self) -> None:
        self.conn.close()


def _check_identifier(name: str) -> None:
    if not name.replace("_", "").isalnum():
        raise ValueError(f"Unsafe table name: {name!r}")


class SqliteStore(Store):
    def __init__(self, db: SqliteDatabase, table: str):
        self.db = db
        self.table = table
        db.ensure_table(table)

    def get(self, key: str) -> Optional[str]:
        row = self.db.conn.execute(f'SELECT data FROM "{self.table}" WHERE id = ?', (key,)).fetchone()
        return row[0] if row else None

    def put(self, key: str, payload: str) -> None:
        # Upsert that keeps the original insertion order (seq) on update.
        self.db.conn.execute(
            f'INSERT INTO "{self.table}" (id, data) VALUES (?, ?) '
            "ON CONFLICT(id) DO UPDATE SET data = excluded.data",
            (key, payload),
        )

    def delete(self, key: str) -> bool:
        cur = self.db.conn.execute(f'DELETE FROM "{self.table}" WHERE id = ?', (key,))
        return cur.rowcount > 0

    def values(self) -> list[str]:
        rows = self.db.conn.execute(f'SELECT data FROM "{self.table}" ORDER BY seq').fetchall()
        return [r[0] for r in rows]

    def last(self) -> Optional[str]:
        row = self.db.conn.execute(
            f'SELECT data FROM "{self.table}" ORDER BY seq DESC LIMIT 1'
        ).fetchone()
        return row[0] if row else None

    def count(self) -> int:
        return self.db.conn.execute(f'SELECT COUNT(*) FROM "{self.table}"').fetchone()[0]


class TransactionManager:
    """Re-entrant transaction scope shared by every store of one app.

    The lock also serialises concurrent requests, which keeps multi-step
    service operations (e.g. dispense + stock deduction) consistent.
    """

    def __init__(self, memory_stores: list[MemoryStore], db: Optional[SqliteDatabase]):
        self._memory_stores = memory_stores
        self._db = db
        self._lock = threading.RLock()
        self._depth = 0
        self._snapshots: list[dict[str, str]] = []

    def __enter__(self):
        self._lock.acquire()
        if self._depth == 0:
            self._snapshots = [s.snapshot() for s in self._memory_stores]
            if self._db is not None:
                self._db.begin()
        self._depth += 1
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self._depth -= 1
            if self._depth == 0:
                if exc_type is None:
                    if self._db is not None:
                        self._db.commit()
                else:
                    for store, snap in zip(self._memory_stores, self._snapshots):
                        store.restore(snap)
                    if self._db is not None:
                        self._db.rollback()
                self._snapshots = []
        finally:
            self._lock.release()
        return False
