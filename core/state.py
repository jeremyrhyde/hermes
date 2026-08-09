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
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import aiosqlite

from services.migrations import MIGRATIONS_DIR, apply_migrations

if TYPE_CHECKING:  # pragma: no cover
    from schemas.article import ArticleRef, Summary
    from schemas.scoring import Score
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


def _placeholders(n: int) -> str:
    """``?`` placeholders for an ``IN`` clause of *n* bound values.

    Only the placeholders are ever built by string work; the values themselves
    stay bound, because category names arrive from the query string.
    """

    return ",".join("?" * n)


_ALL_OF_CATEGORIES = """
    SELECT article_id FROM article_categories
     WHERE category IN ({placeholders})
     GROUP BY article_id
    HAVING COUNT(DISTINCT category) = ?
"""
"""Article ids carrying *every* one of the bound categories — the AND semantics.

``DISTINCT`` guards the count even though ``PRIMARY KEY (article_id, category)``
already rules out duplicate rows.
"""


_PENDING_STAGES: dict[str, tuple[str, str]] = {
    "extract": ("extracted_at", ""),
    "summarize": (
        "summarized_at",
        "AND extracted_at IS NOT NULL AND unusable_at IS NULL",
    ),
    "score": (
        "scored_at",
        "AND summarized_at IS NOT NULL AND unusable_at IS NULL",
    ),
}
"""Per stage: the checkpoint column, and what must already be true to run it.

Spelled out per stage rather than derived, because the preconditions do not
follow from the checkpoint. Deriving them — "extract has none, everything else
needs an extraction" — happens to hold for two stages and silently mis-gates the
third: scoring would then run on an extracted but unsummarized article and score
a summary that does not exist.

The unusable exclusion is load-bearing here and nowhere else. This query selects
for `<checkpoint> IS NULL`, which is precisely the state an unusable article is
permanently in, so without it every one of them is returned as pending on every
poll of its source, forever. The same condition on feed_items and
category_counts is defensive — there, `summarized_at IS NOT NULL` already
excludes them.
"""


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
        """Write the summary, its categories, and the stage checkpoint as one unit.

        Categories are deleted and re-inserted rather than merged, so
        re-summarizing replaces the previous set instead of accumulating stale
        tags. The summarizer has already lowercased, de-duplicated, capped, and
        validated them against the configured vocabulary (spec 5.2), so they are
        stored verbatim.

        The ``summarized_at`` checkpoint is written LAST, after the categories.
        There is no rollback here, and the pipeline's error handler commits on
        this same connection, so a failure part-way through still lands what ran
        before it. Setting the checkpoint before the tag write would therefore
        strand a half-written article: ``articles_pending("summarize")`` keys on
        ``summarized_at IS NULL`` and would never retry it. In this order a
        failure leaves the checkpoint NULL and the next pass heals the row
        through the ``ON CONFLICT`` upsert above.
        """

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
            "DELETE FROM article_categories WHERE article_id = ?", (article_id,)
        )
        if summary.categories:
            await self.db.executemany(
                "INSERT INTO article_categories (article_id, category) VALUES (?, ?)",
                [(article_id, category) for category in summary.categories],
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

    async def save_score(
        self, article_id: int, score: "Score", scored_at: datetime
    ) -> None:
        """Append the score row, then set the stage checkpoint, as one unit.

        Append-only, like ratings: rescoring under a new rubric or profile adds
        a row rather than overwriting, so a score stays comparable to the pair
        that produced it and the history of how the ranking moved survives.
        ``feed_items`` resolves the latest by ``created_at DESC``.

        ``Score.value`` maps onto the ``scores.score`` column here — the one
        place the two names meet. See :mod:`schemas.scoring` for why the model
        does not call it ``score``.

        The ``scored_at`` checkpoint is written LAST, for the reason spelled out
        in :meth:`save_summary`: a checkpoint that lands before its data leaves
        an article that looks scored, has no score row, and is never retried.
        """

        import json

        await self.db.execute(
            """
            INSERT INTO scores
                (article_id, score, rationale, rubric_version, profile_version,
                 signals, created_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                article_id, score.value, score.rationale, score.rubric_version,
                score.profile_version, json.dumps(score.signals), iso(scored_at),
                json.dumps(score.metadata),
            ),
        )
        await self.db.execute(
            """
            UPDATE articles
               SET scored_at = ?, last_error = NULL, error_stage = NULL
             WHERE id = ?
            """,
            (iso(scored_at), article_id),
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

    async def articles_pending(
        self, stage: str, limit: int = 50, *, source_id: str | None = None
    ) -> list[int]:
        """Article ids whose *stage* checkpoint is unset. Drives resume-on-restart.

        *source_id* scopes the result to one source's stranded work. A poll of
        source A must not pick up source B's pending articles: it would fetch
        them with A's driver and publish them to the live feed carrying A's
        source ref, so a post would appear under the wrong publication until a
        reload re-read the correct ``source_id`` from the row. It stays optional
        and keyword-only so existing positional calls keep the global view and
        cannot accidentally bind it to *limit*.
        """

        # The column name is interpolated because it is chosen from a fixed
        # internal dict; source_id is user-controlled (it comes from
        # sources.yaml) and is therefore always a bound parameter.
        column, precondition = _PENDING_STAGES[stage]
        source_clause = "AND source_id = ?" if source_id is not None else ""
        params: tuple[object, ...] = (
            (source_id, limit) if source_id is not None else (limit,)
        )
        cur = await self.db.execute(
            f"""
            SELECT id FROM articles
             WHERE {column} IS NULL {precondition} {source_clause}
             ORDER BY COALESCE(published_at, fetched_at) DESC
             LIMIT ?
            """,
            params,
        )
        return [row["id"] for row in await cur.fetchall()]

    # ------------------------------------------------------------------
    # Saved articles
    # ------------------------------------------------------------------
    async def save_article(self, article_id: int, saved_at: datetime) -> bool:
        """Pin *article_id*. Returns whether the article exists.

        ``COALESCE`` keeps the original timestamp, so re-starring an
        already-saved article is a no-op rather than a silent reorder of the
        Saved list.

        The boolean is existence, not change: ``True`` whenever the row is
        there, ``False`` only for an unknown id. ``rowcount`` cannot answer
        that — an UPDATE that writes the same value reports zero rows affected,
        which is precisely the idempotent case that must succeed. ``RETURNING``
        emits a row for every row the WHERE matched, changed or not, so one
        statement answers existence without a second lookup to race against.
        """

        return await self._update_exists(
            """
            UPDATE articles SET saved_at = COALESCE(saved_at, ?)
             WHERE id = ? RETURNING id
            """,
            (iso(saved_at), article_id),
        )

    async def unsave_article(self, article_id: int) -> bool:
        """Unpin *article_id*. Returns whether the article exists.

        Unpinning an article that was never pinned is not an error; see
        :meth:`save_article` for why the boolean means existence.
        """

        return await self._update_exists(
            "UPDATE articles SET saved_at = NULL WHERE id = ? RETURNING id",
            (article_id,),
        )

    # ------------------------------------------------------------------
    # Unusable extractions
    # ------------------------------------------------------------------
    async def mark_unusable(
        self, article_id: int, reason: str, at: datetime
    ) -> None:
        """Record that *article_id* has no usable text, terminally.

        A stored verdict, not a computed one: lowering ``MIN_USABLE_WORDS``
        later changes nothing on its own, because every exclusion keys off this
        column rather than the threshold. The row and its text are retained so a
        future re-evaluation would need no refetch, but nothing clears this
        automatically.
        """

        await self.db.execute(
            "UPDATE articles SET unusable_at = ?, unusable_reason = ? WHERE id = ?",
            (iso(at), reason, article_id),
        )
        await self.db.commit()

    async def unusable_counts(self) -> dict[str, int]:
        """Map source id to its unusable article count, non-zero entries only.

        A GROUP BY cannot produce a row for a source with none, so callers that
        need a zero must supply it themselves.
        """

        cur = await self.db.execute(
            """
            SELECT source_id, COUNT(*) AS n FROM articles
             WHERE unusable_at IS NOT NULL
             GROUP BY source_id
            """
        )
        return {row["source_id"]: row["n"] for row in await cur.fetchall()}

    # ------------------------------------------------------------------
    # Feedback
    # ------------------------------------------------------------------
    async def rate_article(
        self, article_id: int, value: int, at: datetime
    ) -> bool:
        """Append a ±1 rating. Returns whether the article exists.

        Append-only with latest-wins, which ``feed_items`` already resolves via
        ``ORDER BY created_at DESC LIMIT 1``. Appending rather than upserting
        keeps the history of how an opinion changed — which the profile
        distillation may want — and avoids an upsert race between two rapid
        clicks.

        Existence is checked first because an INSERT into ``ratings`` would
        otherwise fail on the foreign key, and a constraint error is a worse way
        to learn about an unknown id than a boolean.
        """

        if not await self._article_exists(article_id):
            return False
        await self.db.execute(
            "INSERT INTO ratings (article_id, value, created_at) VALUES (?, ?, ?)",
            (article_id, value, iso(at)),
        )
        await self.db.commit()
        return True

    async def clear_rating(self, article_id: int) -> bool:
        """Remove every rating for *article_id*. Returns whether it exists.

        ``CHECK (value IN (-1, 1))`` makes a neutral rating unrepresentable, so
        undoing one means deleting rather than writing a zero. Clearing an
        unrated article is a successful no-op — the boolean is existence, as it
        is for :meth:`unsave_article`.
        """

        if not await self._article_exists(article_id):
            return False
        await self.db.execute(
            "DELETE FROM ratings WHERE article_id = ?", (article_id,)
        )
        await self.db.commit()
        return True

    async def record_interaction(
        self, article_id: int, kind: str, at: datetime
    ) -> bool:
        """Append an interaction event. Returns whether the article exists.

        Never deduplicated: expanding the same article three times is signal.
        """

        if not await self._article_exists(article_id):
            return False
        await self.db.execute(
            "INSERT INTO interactions (article_id, kind, created_at) VALUES (?, ?, ?)",
            (article_id, kind, iso(at)),
        )
        await self.db.commit()
        return True

    # ------------------------------------------------------------------
    # Taste profiles
    # ------------------------------------------------------------------
    async def latest_profile(self) -> tuple[str, str] | None:
        """The newest *approved* profile version and its body, or ``None``.

        A query rather than a single-row lookup: phase 4 appends distilled
        versions alongside the stated one, so "current" is the most recently
        created row — but only among approved ones.

        ``approved_at IS NOT NULL`` is the whole point of the approval gate. The
        spec defines a NULL ``approved_at`` as *proposed, not active*, so
        without this clause the first distillation phase 4 proposes would become
        the live profile the moment it was written, before anyone reviewed it —
        silently changing what every subsequent score means. Seeded profiles are
        approved on write (there is nobody but the author to approve a
        hand-written one), so this filter excludes nothing today; it exists so
        that phase 4 cannot forget it.
        """

        cur = await self.db.execute(
            """
            SELECT version, body FROM profile_versions
             WHERE approved_at IS NOT NULL
             ORDER BY created_at DESC, rowid DESC LIMIT 1
            """
        )
        row = await cur.fetchone()
        return (row["version"], row["body"]) if row else None

    async def seed_profile(self, version: str, body: str) -> None:
        """Insert only if absent, so the file never clobbers an edited profile.

        The same shape as :meth:`seed_preference`, for the same reason: the file
        on disk is a starting point, and the table is authoritative once
        anything has written to it. ``kind='stated'`` and an ``approved_at`` set
        on insert — a hand-written profile is approved by construction; only a
        distilled one needs review.
        """

        now = iso(utcnow())
        await self.db.execute(
            """
            INSERT OR IGNORE INTO profile_versions
                (version, body, kind, created_at, approved_at)
            VALUES (?, ?, 'stated', ?, ?)
            """,
            (version, body, now, now),
        )
        await self.db.commit()

    async def _article_exists(self, article_id: int) -> bool:
        cur = await self.db.execute(
            "SELECT 1 FROM articles WHERE id = ?", (article_id,)
        )
        return await cur.fetchone() is not None

    async def _update_exists(self, sql: str, params: tuple[object, ...]) -> bool:
        """Run an ``UPDATE ... RETURNING`` and report whether it matched a row."""

        cur = await self.db.execute(sql, params)
        matched = bool(await cur.fetchall())
        await self.db.commit()
        return matched

    # ------------------------------------------------------------------
    # Feed
    # ------------------------------------------------------------------
    async def feed_items(
        self,
        limit: int = 50,
        offset: int = 0,
        categories: list[str] | None = None,
        saved_only: bool = False,
    ) -> list[dict]:
        """Summarized articles, newest first, joined with source identity.

        Returns plain dicts; assembling the FeedItem DTO is the API layer's job
        (spec section 12.5) so the wire format can evolve independently.

        *categories* narrows the result to articles carrying **all** of them; an
        empty list or ``None`` is the unfiltered feed. Each row's ``categories``
        key holds every category that article carries, not just the filtered
        ones — the live-update path needs the full set to decide whether an
        arriving article satisfies the active filters.

        Each row's ``saved`` key is ``saved_at IS NOT NULL``. It needs no join —
        the flag lives on the ``articles`` row this query already selects.

        *saved_only* narrows to saved articles **and** switches the ordering to
        newest-saved first, ignoring publication date. The coupling is
        deliberate: the Saved view has exactly one natural order and the feed
        has another, so a separate ``order_by`` parameter would be a second knob
        that could only ever be turned in lockstep with this one. It composes
        with *categories* — both narrow the same result.
        """

        params: list[object] = []
        category_clause = ""
        if categories:
            all_of = _ALL_OF_CATEGORIES.format(
                placeholders=_placeholders(len(categories))
            )
            category_clause = f"AND a.id IN ({all_of})"
            params.extend(categories)
            params.append(len(categories))
        saved_clause = "AND a.saved_at IS NOT NULL" if saved_only else ""
        order_by = (
            "a.saved_at DESC" if saved_only
            else "COALESCE(a.published_at, a.fetched_at) DESC"
        )
        params.extend((limit, offset))

        cur = await self.db.execute(
            f"""
            SELECT a.id            AS article_id,
                   a.canonical_url AS url,
                   a.published_at  AS published_at,
                   a.saved_at IS NOT NULL AS saved,
                   s.headline      AS headline,
                   s.bullets_json  AS bullets_json,
                   src.id          AS source_id,
                   src.name        AS source_name,
                   src.type        AS source_type,
                   (SELECT value FROM ratings r
                     WHERE r.article_id = a.id
                     ORDER BY r.created_at DESC, r.id DESC LIMIT 1) AS rating,
                   (SELECT score FROM scores sc
                     WHERE sc.article_id = a.id
                     -- Tie-break on id, not just timestamp. Both tables are
                     -- append-only, so "latest" on a colliding created_at
                     -- otherwise resolves by rowid *ascending* and the OLDER
                     -- row wins — a re-score or re-rating silently discarded,
                     -- which is the hardest kind of failure to notice. A batch
                     -- re-score stamping one `now` across the run is exactly
                     -- how that collision happens.
                     ORDER BY sc.created_at DESC, sc.id DESC LIMIT 1) AS score
              FROM articles a
              JOIN summaries s ON s.article_id = a.id
              JOIN sources  src ON src.id = a.source_id
             WHERE a.summarized_at IS NOT NULL
               -- Redundant today: an unusable article is never summarized, so
               -- the line above already excludes it. Kept so the invariant is
               -- local to the read site rather than inferred from another
               -- column. Do not treat this and the one in articles_pending as
               -- the same kind of check — that one is load-bearing.
               AND a.unusable_at IS NULL
             {category_clause}
             {saved_clause}
             ORDER BY {order_by}
             LIMIT ? OFFSET ?
            """,
            params,
        )
        rows = [dict(row) for row in await cur.fetchall()]
        by_article = await self._categories_for(row["article_id"] for row in rows)
        for row in rows:
            row["categories"] = by_article.get(row["article_id"], [])
            # SQLite has no boolean type; the comparison comes back as 0/1.
            row["saved"] = bool(row["saved"])
        return rows

    async def _categories_for(
        self, article_ids: Iterable[int]
    ) -> dict[int, list[str]]:
        """Map article id to its sorted categories, in one query for the page.

        A second query over the page's ids rather than a per-row lookup, which
        would be an N+1; and rather than GROUP_CONCAT in the feed query, whose
        result would have to be split back apart on a separator that the data is
        not guaranteed to exclude.
        """

        ids = list(article_ids)
        if not ids:
            return {}
        cur = await self.db.execute(
            f"""
            SELECT article_id, category FROM article_categories
             WHERE article_id IN ({_placeholders(len(ids))})
             ORDER BY category
            """,
            ids,
        )
        by_article: dict[int, list[str]] = {}
        for row in await cur.fetchall():
            by_article.setdefault(row["article_id"], []).append(row["category"])
        return by_article

    async def category_counts(
        self,
        filters: list[str],
        selected: list[str] | None = None,
        saved_only: bool = False,
    ) -> dict[str, int]:
        """How many articles would remain if each filter were *also* selected.

        Counts are contextual, not global: under AND semantics a global count
        says nothing about the overlap, and the failure mode we care about is
        clicking a healthy-looking pair and landing on an empty feed. Every
        entry in *filters* is present in the result, zeros included — a dead end
        must render as a disabled button, not disappear from the row.

        Only summarized articles are counted, matching what the feed can return.

        *saved_only* restricts the counted population to saved articles so the
        filter row reads against the Saved view it sits above. Zero back-fill is
        unchanged: a scoped dead end still renders disabled rather than vanishing.
        """

        counts = {category: 0 for category in filters}
        if not filters:
            return counts

        params: list[object] = list(filters)
        selection_clause = ""
        if selected:
            all_of = _ALL_OF_CATEGORIES.format(
                placeholders=_placeholders(len(selected))
            )
            selection_clause = f"AND ac.article_id IN ({all_of})"
            params.extend(selected)
            params.append(len(selected))
        # A predicate on the join that already exists, not a second join.
        saved_clause = "AND a.saved_at IS NOT NULL" if saved_only else ""

        cur = await self.db.execute(
            f"""
            SELECT ac.category AS category,
                   COUNT(DISTINCT ac.article_id) AS count
              FROM article_categories ac
              JOIN articles a ON a.id = ac.article_id
             WHERE ac.category IN ({_placeholders(len(filters))})
               AND a.summarized_at IS NOT NULL
               AND a.unusable_at IS NULL   -- defensive; see feed_items
               {saved_clause}
               {selection_clause}
             GROUP BY ac.category
            """,
            params,
        )
        for row in await cur.fetchall():
            counts[row["category"]] = row["count"]
        return counts
