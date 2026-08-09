"""Stage orchestration.

Stages are checkpointed independently in the database (spec section 3.1):
a summarization failure must not cost the fetch, and re-scoring must not
re-fetch. Every stage checks its checkpoint before running, so the whole
pipeline is re-entrant — crash mid-run, restart, resume where it stopped.

Failure isolation is per article. One bad article records a stage error and the
batch continues; the article is retried on a later pass.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime

from core.events import EventBus
from core.state import StateStore, parse_iso, utcnow
from schemas.article import Article, ArticleRef
from schemas.events import Event, EventType
from schemas.source import SourceConfig
from services.extraction import (
    MIN_USABLE_WORDS,
    extract_text,
    unusable_reason,
)
from services.sources.base import PollState
from services.sources.registry import SourceRegistry
from services.summarizer import Summarizer
from services.urls import canonicalize_url

logger = logging.getLogger(__name__)

#: How many articles may be summarized concurrently. Summarization is the only
#: stage that touches a paid API, so it is the only one worth bounding.
SUMMARIZE_CONCURRENCY = 4


@dataclass
class PollOutcome:
    """What one source poll produced. Consumed by the poller for scheduling."""

    not_modified: bool = False
    new_articles: int = 0
    failed: int = 0
    etag: str | None = None
    last_modified: str | None = None
    content_hash: str | None = None
    retry_after_seconds: int | None = None
    ttl_seconds: int | None = None


class Pipeline:
    def __init__(
        self,
        *,
        store: StateStore,
        registry: SourceRegistry,
        summarizer: Summarizer,
        bus: EventBus,
    ) -> None:
        self._store = store
        self._registry = registry
        self._summarizer = summarizer
        self._bus = bus
        self._summarize_sem = asyncio.Semaphore(SUMMARIZE_CONCURRENCY)

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------
    async def process_source(
        self, source: SourceConfig, state: PollState, now: datetime | None = None
    ) -> PollOutcome:
        now = now or utcnow()
        driver = self._registry.for_source(source)

        result = await driver.discover(source, state)

        if result.not_modified:
            return PollOutcome(
                not_modified=True,
                etag=result.etag,
                last_modified=result.last_modified,
                content_hash=result.content_hash,
                retry_after_seconds=result.retry_after_seconds,
                ttl_seconds=result.ttl_seconds,
            )

        new_ids: list[int] = []
        for ref in result.refs:
            article_id = await self._ingest(ref, now)
            if article_id is not None:
                new_ids.append(article_id)

        # Include anything left pending from a previous crashed run. Both stages
        # are recovered: an article can be stranded with no extraction (crash
        # between ingest and extract) or extracted but not summarized (crash
        # mid-summarize, which a transient summarizer API error is enough to
        # cause). Neither re-enters via `new_ids`, because the row already
        # exists and `ingest_article` returns None for it, so without these two
        # queries a stranded article would never be retried at all.
        #
        # Both are scoped to this source: another source's pending articles must
        # not be fetched with this driver or published under this source's ref.
        pending_extract = await self._store.articles_pending(
            "extract", limit=50, source_id=source.id
        )
        pending_summarize = await self._store.articles_pending(
            "summarize", limit=50, source_id=source.id
        )
        to_process = list(dict.fromkeys(new_ids + pending_extract + pending_summarize))

        failures = 0
        for article_id in to_process:
            ok = await self._advance(article_id, source, result.refs, now)
            if not ok:
                failures += 1

        return PollOutcome(
            not_modified=False,
            new_articles=len(new_ids),
            failed=failures,
            etag=result.etag,
            last_modified=result.last_modified,
            content_hash=result.content_hash,
            ttl_seconds=result.ttl_seconds,
        )

    # ------------------------------------------------------------------
    # Stages
    # ------------------------------------------------------------------
    async def _ingest(self, ref: ArticleRef, now: datetime) -> int | None:
        canonical = canonicalize_url(ref.url)
        article_id = await self._store.ingest_article(ref, canonical, now)
        if article_id is None:
            return None

        await self._publish(
            EventType.ARTICLE_INGESTED,
            subject=str(article_id),
            data={
                "article_id": article_id,
                "title": ref.title,
                "source_id": ref.source_id,
            },
        )
        return article_id

    async def _advance(
        self,
        article_id: int,
        source: SourceConfig,
        refs: list[ArticleRef],
        now: datetime,
    ) -> bool:
        """Run extract then summarize for one article. Returns False on failure."""

        row = await self._store.get_article_row(article_id)
        if row is None:
            return False

        if row["extracted_at"] is None:
            try:
                await self._extract(article_id, row, source, refs, now)
            except Exception as exc:
                logger.exception("pipeline: extract failed for %d", article_id)
                await self._fail(article_id, "extract", exc)
                return False
            row = await self._store.get_article_row(article_id)

        # Terminal, and not a failure: the extract stage decided we do not have
        # this article's text. Returning True keeps it out of the poll's failure
        # count, which is reserved for things worth retrying.
        if row["unusable_at"] is not None:
            return True

        if row["summarized_at"] is None:
            try:
                async with self._summarize_sem:
                    await self._summarize(article_id, row, source, now)
            except Exception as exc:
                logger.warning("pipeline: summarize failed for %d: %s", article_id, exc)
                await self._fail(article_id, "summarize", exc)
                return False

        return True

    async def _extract(
        self,
        article_id: int,
        row,
        source: SourceConfig,
        refs: list[ArticleRef],
        now: datetime,
    ) -> None:
        """Prefer feed-provided content; refetch only when it is missing or thin."""

        ref = next((r for r in refs if r.guid == row["guid"]), None)
        html = ref.summary_html if ref else None
        result = extract_text(html or "")

        if result.word_count < MIN_USABLE_WORDS:
            logger.info(
                "pipeline: feed content for %d is thin (%d words), refetching",
                article_id, result.word_count,
            )
            driver = self._registry.for_source(source)
            fetch_ref = ref or ArticleRef(
                source_id=source.id, guid=row["guid"],
                url=row["canonical_url"], title=row["title"],
            )
            html = await driver.fetch_html(fetch_ref)
            result = extract_text(html)

        # Still too thin after the one refetch that exists to rescue it: we do
        # not have this article's text and never will without credentials, which
        # the spec rules out. Save what we got so the row leaves the extract
        # queue and a future re-evaluation needs no refetch, mark it terminally
        # unusable, and stop. Summarizing it anyway is how #17 got five
        # <UNKNOWN> bullets and #26 got five confident sentences about other
        # articles' teasers — the second being far worse, because a reader
        # cannot tell it is fabricated and neither can the scorer.
        #
        # A refetch that *throws* is a different thing: that stays on the error
        # path and retries, because it may well be transient.
        if result.word_count < MIN_USABLE_WORDS:
            reason = unusable_reason(result)
            logger.info(
                "pipeline: article %d is unusable (%s); skipping summarization",
                article_id, reason,
            )
            await self._store.save_extraction(
                article_id,
                text=result.text,
                word_count=result.word_count,
                raw_html=html,
                extracted_at=now,
            )
            await self._store.mark_unusable(article_id, reason, now)
            return

        await self._store.save_extraction(
            article_id,
            text=result.text,
            word_count=result.word_count,
            raw_html=html,
            extracted_at=now,
        )

    async def _summarize(
        self, article_id: int, row, source: SourceConfig, now: datetime
    ) -> None:
        article = Article(
            id=row["id"],
            source_id=row["source_id"],
            guid=row["guid"],
            canonical_url=row["canonical_url"],
            title=row["title"],
            author=row["author"],
            published_at=parse_iso(row["published_at"]),
            fetched_at=parse_iso(row["fetched_at"]),
            text=row["text"],
            word_count=row["word_count"],
        )

        summary = await self._summarizer.summarize(article)
        await self._store.save_summary(article_id, summary, summarized_at=now)

        await self._publish(
            EventType.ARTICLE_SUMMARIZED,
            subject=str(article_id),
            data={
                "item": {
                    "article_id": article_id,
                    "headline": summary.headline,
                    "bullets": summary.bullets,
                    "categories": summary.categories,
                    "url": article.canonical_url,
                    "published_at": row["published_at"],
                    "source": source.to_ref().model_dump(mode="json"),
                }
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    async def _fail(self, article_id: int, stage: str, exc: Exception) -> None:
        await self._store.record_article_error(article_id, stage, str(exc))
        await self._publish(
            EventType.PIPELINE_ERROR,
            subject=str(article_id),
            data={"stage": stage, "subject": str(article_id), "error": str(exc)},
        )

    async def _publish(self, event_type: EventType, subject: str, data: dict) -> None:
        await self._bus.publish(
            Event(type=event_type, subject=subject, data=data, source="pipeline")
        )
