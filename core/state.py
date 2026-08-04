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
from typing import TYPE_CHECKING

import aiosqlite

from services.migrations import MIGRATIONS_DIR, apply_migrations

if TYPE_CHECKING:  # pragma: no cover
    from schemas.article import ArticleRef, Summary
    from schemas.source import SourceConfig

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

    # ------------------------------------------------------------------
    # Sources
    # ------------------------------------------------------------------
    async def upsert_source(self, cfg: "SourceConfig") -> None:
        """Insert or update the declarative fields only.

        Poll state (etag, last_modified, content_hash, error_count, next_poll_at)
        is deliberately NOT touched — re-reading sources.yaml on every restart
        must not discard conditional-GET state, or every restart would refetch
        every feed in full.
        """

        await self.db.execute(
            """
            INSERT INTO sources (id, type, name, feed_url, enabled)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                type = excluded.type,
                name = excluded.name,
                feed_url = excluded.feed_url,
                enabled = excluded.enabled
            """,
            (cfg.id, cfg.type, cfg.name, cfg.feed_url, int(cfg.enabled)),
        )
        await self.db.commit()

    async def get_source_row(self, source_id: str) -> aiosqlite.Row | None:
        cur = await self.db.execute("SELECT * FROM sources WHERE id = ?", (source_id,))
        return await cur.fetchone()

    async def due_sources(self, now: datetime) -> list["SourceConfig"]:
        """Enabled sources whose next poll is due and which are not disabled."""

        from schemas.source import SourceConfig  # local import avoids a cycle

        cur = await self.db.execute(
            """
            SELECT * FROM sources
            WHERE enabled = 1
              AND (next_poll_at IS NULL OR next_poll_at <= ?)
              AND (disabled_until IS NULL OR disabled_until <= ?)
            ORDER BY COALESCE(next_poll_at, '')
            """,
            (iso(now), iso(now)),
        )
        return [
            SourceConfig(
                id=row["id"], type=row["type"], name=row["name"],
                feed_url=row["feed_url"], enabled=bool(row["enabled"]),
            )
            for row in await cur.fetchall()
        ]

    async def update_source_poll_state(
        self,
        source_id: str,
        *,
        etag: str | None,
        last_modified: str | None,
        content_hash: str | None,
        error_count: int,
        next_poll_at: datetime,
    ) -> None:
        await self.db.execute(
            """
            UPDATE sources
               SET etag = ?, last_modified = ?, content_hash = ?,
                   error_count = ?, next_poll_at = ?, last_polled_at = ?
             WHERE id = ?
            """,
            (
                etag, last_modified, content_hash, error_count,
                iso(next_poll_at), iso(utcnow()), source_id,
            ),
        )
        await self.db.commit()

    async def set_source_disabled_until(
        self, source_id: str, until: datetime | None
    ) -> None:
        await self.db.execute(
            "UPDATE sources SET disabled_until = ? WHERE id = ?",
            (iso(until), source_id),
        )
        await self.db.commit()

    async def all_source_rows(self) -> list[aiosqlite.Row]:
        cur = await self.db.execute("SELECT * FROM sources ORDER BY name")
        return list(await cur.fetchall())

    # ------------------------------------------------------------------
    # Articles
    # ------------------------------------------------------------------
    async def ingest_article(
        self, ref: "ArticleRef", canonical_url: str, fetched_at: datetime
    ) -> int | None:
        """Insert a new article. Returns its id, or ``None`` if already present.

        Idempotent by ``UNIQUE(source_id, guid)`` and ``UNIQUE(canonical_url)``.
        Returning ``None`` for a duplicate lets the caller skip the whole
        downstream pipeline without a second query.
        """

        cur = await self.db.execute(
            """
            INSERT OR IGNORE INTO articles
                (source_id, guid, canonical_url, title, author,
                 published_at, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ref.source_id, ref.guid, canonical_url, ref.title, ref.author,
                iso(ref.published_at), iso(fetched_at),
            ),
        )
        await self.db.commit()
        return cur.lastrowid if cur.rowcount else None

    async def get_article_row(self, article_id: int) -> aiosqlite.Row | None:
        cur = await self.db.execute(
            "SELECT * FROM articles WHERE id = ?", (article_id,)
        )
        return await cur.fetchone()

    async def save_extraction(
        self,
        article_id: int,
        text: str,
        word_count: int,
        raw_html: str | None,
        extracted_at: datetime,
    ) -> None:
        await self.db.execute(
            """
            UPDATE articles
               SET text = ?, word_count = ?, raw_html = ?, extracted_at = ?,
                   last_error = NULL, error_stage = NULL
             WHERE id = ?
            """,
            (text, word_count, raw_html, iso(extracted_at), article_id),
        )
        await self.db.commit()

    async def save_summary(
        self, article_id: int, summary: "Summary", summarized_at: datetime
    ) -> None:
        import json

        await self.db.execute(
            """
            INSERT INTO summaries
                (article_id, headline, bullets_json, model, prompt_version,
                 created_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(article_id) DO UPDATE SET
                headline = excluded.headline,
                bullets_json = excluded.bullets_json,
                model = excluded.model,
                prompt_version = excluded.prompt_version,
                created_at = excluded.created_at
            """,
            (
                article_id, summary.headline, json.dumps(summary.bullets),
                summary.model, summary.prompt_version, iso(summarized_at),
                json.dumps(summary.metadata),
            ),
        )
        await self.db.execute(
            """
            UPDATE articles
               SET summarized_at = ?, last_error = NULL, error_stage = NULL
             WHERE id = ?
            """,
            (iso(summarized_at), article_id),
        )
        await self.db.commit()

    async def record_article_error(
        self, article_id: int, stage: str, error: str
    ) -> None:
        """Record a stage failure without clearing earlier checkpoints."""

        await self.db.execute(
            "UPDATE articles SET last_error = ?, error_stage = ? WHERE id = ?",
            (error[:2000], stage, article_id),
        )
        await self.db.commit()

    async def articles_pending(self, stage: str, limit: int = 50) -> list[int]:
        """Article ids whose *stage* checkpoint is unset. Drives resume-on-restart."""

        column = {"extract": "extracted_at", "summarize": "summarized_at"}[stage]
        precondition = "" if stage == "extract" else "AND extracted_at IS NOT NULL"
        cur = await self.db.execute(
            f"""
            SELECT id FROM articles
             WHERE {column} IS NULL {precondition}
             ORDER BY COALESCE(published_at, fetched_at) DESC
             LIMIT ?
            """,
            (limit,),
        )
        return [row["id"] for row in await cur.fetchall()]
