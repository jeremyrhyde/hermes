"""Migration runner: forward-only, idempotent, keyed on PRAGMA user_version."""

from __future__ import annotations

import aiosqlite

from services.migrations import MIGRATIONS_DIR, apply_migrations


async def _table_names(db: aiosqlite.Connection) -> set[str]:
    cur = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {row[0] for row in await cur.fetchall()}


async def test_apply_migrations_creates_schema(tmp_path) -> None:
    async with aiosqlite.connect(tmp_path / "t.db") as db:
        version = await apply_migrations(db, MIGRATIONS_DIR)

        assert version == 1
        names = await _table_names(db)
        assert {
            "sources", "articles", "summaries", "scores",
            "ratings", "interactions", "profile_versions", "preferences",
        } <= names


async def test_apply_migrations_is_idempotent(tmp_path) -> None:
    """Running twice must be a no-op, not an error."""
    async with aiosqlite.connect(tmp_path / "t.db") as db:
        first = await apply_migrations(db, MIGRATIONS_DIR)
        second = await apply_migrations(db, MIGRATIONS_DIR)

        assert first == second == 1


async def test_metadata_columns_exist_from_migration_001(tmp_path) -> None:
    """Spec 12.2: metadata columns ship in 001, not retrofitted later."""
    async with aiosqlite.connect(tmp_path / "t.db") as db:
        await apply_migrations(db, MIGRATIONS_DIR)

        for table in ("articles", "summaries", "scores"):
            cur = await db.execute(f"PRAGMA table_info({table})")
            cols = {row[1] for row in await cur.fetchall()}
            assert "metadata" in cols, f"{table} is missing metadata"

        cur = await db.execute("PRAGMA table_info(scores)")
        cols = {row[1] for row in await cur.fetchall()}
        assert "signals" in cols, "scores is missing signals (spec 12.4)"
