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

        assert version == 5
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

        assert first == second == 5


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


async def test_runner_skips_already_applied_migrations(tmp_path) -> None:
    """The version filter must actually skip — not rely on IF NOT EXISTS.

    Phase 1's migrations are all idempotent by construction, so a broken
    version filter is invisible. This uses a deliberately NON-idempotent
    migration: re-running it raises. If the filter regresses, this fails.
    """
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "001_only.sql").write_text(
        "CREATE TABLE widget (id INTEGER PRIMARY KEY);", encoding="utf-8"
    )

    async with aiosqlite.connect(tmp_path / "t.db") as db:
        first = await apply_migrations(db, migrations)
        # A second run must not re-execute 001. CREATE TABLE without
        # IF NOT EXISTS raises on the second attempt, so a broken filter
        # surfaces as OperationalError rather than a silent no-op.
        second = await apply_migrations(db, migrations)

    assert first == second == 1


async def test_runner_applies_only_pending_migrations(tmp_path) -> None:
    """Adding 002 later applies 002 alone, without re-touching 001.

    001 deliberately has no IF NOT EXISTS (same trick as the test above):
    if the version filter regresses and re-applies 001 alongside 002, the
    second apply_migrations call raises instead of silently double-running.
    Adding IF NOT EXISTS here would make this test pass even with a broken
    filter, defeating the point.
    """
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "001_only.sql").write_text(
        "CREATE TABLE widget (id INTEGER PRIMARY KEY);", encoding="utf-8"
    )

    db_path = tmp_path / "t.db"
    async with aiosqlite.connect(db_path) as db:
        assert await apply_migrations(db, migrations) == 1

    # 002 lands after 001 has already run — the realistic upgrade path.
    (migrations / "002_more.sql").write_text(
        "CREATE TABLE gadget (id INTEGER PRIMARY KEY);", encoding="utf-8"
    )

    async with aiosqlite.connect(db_path) as db:
        assert await apply_migrations(db, migrations) == 2
        cur = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        names = {row[0] for row in await cur.fetchall()}

    assert {"widget", "gadget"} <= names


async def test_runner_applies_pending_migrations_in_order(tmp_path) -> None:
    """001 and 002 pending simultaneously must run 001 before 002.

    This is the fresh-install / `rm hermes.db` path: every pending migration
    applies in a single pass, so ordering between them is never exercised by
    the other tests here (test 1 has only one file; test 2 applies 001 and
    002 in two separate passes, never together). 002 uses ALTER TABLE ...
    ADD COLUMN against a table 001 creates — SQLite has no IF NOT EXISTS for
    ALTER TABLE ADD COLUMN, so this is deliberately non-idempotent and
    order-dependent: under reverse order it fails with "no such table:
    widget" rather than passing by accident.
    """
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "001_widget.sql").write_text(
        "CREATE TABLE widget (id INTEGER PRIMARY KEY);", encoding="utf-8"
    )
    (migrations / "002_add_column.sql").write_text(
        "ALTER TABLE widget ADD COLUMN label TEXT;", encoding="utf-8"
    )

    async with aiosqlite.connect(tmp_path / "t.db") as db:
        version = await apply_migrations(db, migrations)
        cur = await db.execute("PRAGMA table_info(widget)")
        cols = {row[1] for row in await cur.fetchall()}

    assert version == 2
    assert "label" in cols


async def test_migration_002_creates_article_categories(tmp_path) -> None:
    async with aiosqlite.connect(tmp_path / "t.db") as db:
        version = await apply_migrations(db, MIGRATIONS_DIR)

        assert version == 5
        cur = await db.execute("PRAGMA table_info(article_categories)")
        cols = {row[1] for row in await cur.fetchall()}
        assert {"article_id", "category"} <= cols


async def test_article_categories_cascade_and_dedup(tmp_path) -> None:
    """Composite PK makes tagging idempotent; cascade cleans up."""
    async with aiosqlite.connect(tmp_path / "t.db") as db:
        await apply_migrations(db, MIGRATIONS_DIR)
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute(
            "INSERT INTO sources (id, type, name, feed_url) VALUES "
            "('s1', 'substack', 'S', 'https://s/feed')"
        )
        await db.execute(
            "INSERT INTO articles (id, source_id, guid, canonical_url, title, "
            "fetched_at) VALUES (1, 's1', 'g1', 'https://s/p/1', 'T', 'now')"
        )
        await db.execute(
            "INSERT OR IGNORE INTO article_categories VALUES (1, 'ai')"
        )
        await db.execute(
            "INSERT OR IGNORE INTO article_categories VALUES (1, 'ai')"
        )
        cur = await db.execute("SELECT COUNT(*) FROM article_categories")
        assert (await cur.fetchone())[0] == 1, "composite PK must dedup"

        await db.execute("DELETE FROM articles WHERE id = 1")
        cur = await db.execute("SELECT COUNT(*) FROM article_categories")
        assert (await cur.fetchone())[0] == 0, "cascade must remove tags"


async def test_migration_003_adds_saved_at(tmp_path) -> None:
    async with aiosqlite.connect(tmp_path / "t.db") as db:
        version = await apply_migrations(db, MIGRATIONS_DIR)

        assert version == 5
        cur = await db.execute("PRAGMA table_info(articles)")
        cols = {row[1] for row in await cur.fetchall()}
        assert "saved_at" in cols


async def test_migration_003_is_not_rerun_on_restart(tmp_path) -> None:
    """ADD COLUMN has no IF NOT EXISTS — a re-run raises, so this is the
    first migration where the version-skip logic is load-bearing rather
    than merely correct."""
    db_path = tmp_path / "t.db"
    async with aiosqlite.connect(db_path) as db:
        assert await apply_migrations(db, MIGRATIONS_DIR) == 5

    async with aiosqlite.connect(db_path) as db:
        # Raises OperationalError: duplicate column name if the filter regresses.
        assert await apply_migrations(db, MIGRATIONS_DIR) == 5


async def test_saved_at_defaults_to_null(tmp_path) -> None:
    """Nothing is saved before the user saves it — no backfill."""
    async with aiosqlite.connect(tmp_path / "t.db") as db:
        await apply_migrations(db, MIGRATIONS_DIR)
        await db.execute(
            "INSERT INTO sources (id, type, name, feed_url) VALUES "
            "('s1', 'substack', 'S', 'https://s/feed')"
        )
        await db.execute(
            "INSERT INTO articles (id, source_id, guid, canonical_url, title, "
            "fetched_at) VALUES (1, 's1', 'g1', 'https://s/p/1', 'T', 'now')"
        )
        cur = await db.execute("SELECT saved_at FROM articles WHERE id = 1")
        assert (await cur.fetchone())[0] is None


async def test_saved_index_is_partial(tmp_path) -> None:
    """The saved list orders by this index, and only saved rows belong in it.

    Added to close a gap found reviewing Task 1: asserting the name alone
    would pass against a full index over a mostly-NULL column.
    """
    async with aiosqlite.connect(tmp_path / "t.db") as db:
        await apply_migrations(db, MIGRATIONS_DIR)

        cur = await db.execute(
            "SELECT partial FROM pragma_index_list('articles') WHERE name = ?",
            ("idx_articles_saved",),
        )
        row = await cur.fetchone()

    assert row is not None, "idx_articles_saved is missing from articles"
    assert row[0] == 1, "idx_articles_saved must be a partial index"


async def test_migration_004_adds_unusable_columns(tmp_path) -> None:
    async with aiosqlite.connect(tmp_path / "t.db") as db:
        version = await apply_migrations(db, MIGRATIONS_DIR)

        assert version == 5
        cur = await db.execute("PRAGMA table_info(articles)")
        cols = {row[1] for row in await cur.fetchall()}
        assert {"unusable_at", "unusable_reason"} <= cols


async def test_migration_004_is_not_rerun_on_restart(tmp_path) -> None:
    """Another ADD COLUMN, so the version filter stays load-bearing."""
    db_path = tmp_path / "t.db"
    async with aiosqlite.connect(db_path) as db:
        assert await apply_migrations(db, MIGRATIONS_DIR) == 5

    async with aiosqlite.connect(db_path) as db:
        # Raises OperationalError: duplicate column name if the filter regresses.
        assert await apply_migrations(db, MIGRATIONS_DIR) == 5


async def test_unusable_defaults_to_null(tmp_path) -> None:
    """Articles that predate the gate are not retroactively unusable."""
    async with aiosqlite.connect(tmp_path / "t.db") as db:
        await apply_migrations(db, MIGRATIONS_DIR)
        await db.execute(
            "INSERT INTO sources (id, type, name, feed_url) "
            "VALUES ('s', 'substack', 'S', 'https://x/feed')"
        )
        await db.execute(
            "INSERT INTO articles (id, source_id, guid, canonical_url, title, fetched_at)"
            " VALUES (1, 's', 'g', 'https://x/1', 'T', '2026-08-09T00:00:00+00:00')"
        )
        cur = await db.execute(
            "SELECT unusable_at, unusable_reason FROM articles WHERE id = 1"
        )
        assert await cur.fetchone() == (None, None)


async def test_migration_004_index_is_partial(tmp_path) -> None:
    """A full index over every article would be wasted — unusable rows are rare."""
    async with aiosqlite.connect(tmp_path / "t.db") as db:
        await apply_migrations(db, MIGRATIONS_DIR)
        cur = await db.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'idx_articles_unusable'"
        )
        sql = (await cur.fetchone())[0]
        assert "WHERE unusable_at IS NOT NULL" in sql


async def test_migration_005_adds_rejected_at(tmp_path) -> None:
    async with aiosqlite.connect(tmp_path / "t.db") as db:
        version = await apply_migrations(db, MIGRATIONS_DIR)

        assert version == 5
        cur = await db.execute("PRAGMA table_info(profile_versions)")
        assert "rejected_at" in {row[1] for row in await cur.fetchall()}


async def test_migration_005_is_not_rerun_on_restart(tmp_path) -> None:
    """Another ADD COLUMN, so the version filter stays load-bearing."""
    db_path = tmp_path / "t.db"
    async with aiosqlite.connect(db_path) as db:
        assert await apply_migrations(db, MIGRATIONS_DIR) == 5
    async with aiosqlite.connect(db_path) as db:
        assert await apply_migrations(db, MIGRATIONS_DIR) == 5


async def test_rejected_at_defaults_to_null(tmp_path) -> None:
    """An existing profile is neither approved-by-this-migration nor rejected."""
    async with aiosqlite.connect(tmp_path / "t.db") as db:
        await apply_migrations(db, MIGRATIONS_DIR)
        await db.execute(
            "INSERT INTO profile_versions (version, body, kind, created_at)"
            " VALUES ('profile-v1', 'b', 'stated', '2026-08-09T00:00:00+00:00')"
        )
        cur = await db.execute("SELECT rejected_at FROM profile_versions")
        assert (await cur.fetchone())[0] is None
