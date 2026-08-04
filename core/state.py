"""SQLite persistence.

All SQL lives here. Queries that change together live together; splitting per
table would scatter transaction boundaries. Split by aggregate if this passes
~500 lines.

Timestamps are stored as ISO-8601 strings in UTC. SQLite has no datetime type,
and ISO-8601 sorts lexicographically, so ``ORDER BY published_at DESC`` is
correct without conversion.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import aiosqlite

from services.migrations import MIGRATIONS_DIR, apply_migrations

logger = logging.getLogger(__name__)


def iso(value: datetime | None) -> str | None:
    """Serialize a datetime to a UTC ISO-8601 string."""

    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    """Inverse of :func:`iso`. Returns ``None`` for ``None`` or empty."""

    if not value:
        return None
    return datetime.fromisoformat(value)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StateStore:
    """Owns the aiosqlite connection and every query against it."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("StateStore.start() has not been called")
        return self._db

    async def start(self) -> None:
        """Open the connection, enable foreign keys, apply migrations."""

        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA foreign_keys = ON")
        await self._db.execute("PRAGMA journal_mode = WAL")
        version = await apply_migrations(self._db, MIGRATIONS_DIR)
        logger.info("state: opened %s at schema v%d", self._db_path, version)

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    # ------------------------------------------------------------------
    # Preferences — runtime-editable knobs (spec 3.4)
    # ------------------------------------------------------------------
    async def get_preference(self, key: str, default: str | None = None) -> str | None:
        cur = await self.db.execute(
            "SELECT value FROM preferences WHERE key = ?", (key,)
        )
        row = await cur.fetchone()
        return row["value"] if row else default

    async def set_preference(self, key: str, value: str) -> None:
        await self.db.execute(
            """
            INSERT INTO preferences (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                           updated_at = excluded.updated_at
            """,
            (key, value, iso(utcnow())),
        )
        await self.db.commit()

    async def seed_preference(self, key: str, value: str) -> None:
        """Insert only if absent, so env defaults never clobber user edits."""

        await self.db.execute(
            "INSERT OR IGNORE INTO preferences (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, iso(utcnow())),
        )
        await self.db.commit()
