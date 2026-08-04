"""Pipeline: checkpoint guards, idempotent re-runs, per-article failure isolation."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from core.events import EventBus
from core.state import StateStore
from schemas.article import ArticleRef, DiscoverResult, Summary
from schemas.events import Event, EventType
from schemas.source import SourceConfig
from services.pipeline import Pipeline
from services.sources.base import PollState, SourceDriver
from services.sources.registry import SourceRegistry
from services.summarizer import SummarizationError
from services.urls import canonicalize_url

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
CFG = SourceConfig(id="acx", type="stub", name="ACX", feed_url="https://x/feed")


class StubDriver(SourceDriver):
    source_type = "stub"

    def __init__(self, refs: list[ArticleRef], html: str = "<p>body</p>") -> None:
        self._refs = refs
        self._html = html
        self.fetch_calls = 0

    async def discover(self, source, state) -> DiscoverResult:
        return DiscoverResult(refs=self._refs, content_hash="h1")

    async def fetch_html(self, ref) -> str:
        self.fetch_calls += 1
        return self._html


class StubSummarizer:
    def __init__(self, fail: bool = False) -> None:
        self.calls = 0
        self._fail = fail

    async def summarize(self, article) -> Summary:
        self.calls += 1
        if self._fail:
            raise SummarizationError("stub failure")
        return Summary(
            headline=f"H{article.id}", bullets=["a"] * 5,
            model="stub", prompt_version="v1",
        )


def _refs(n: int) -> list[ArticleRef]:
    return [
        ArticleRef(
            source_id="acx", guid=f"g{i}",
            url=f"https://acx.substack.com/p/{i}", title=f"Post {i}",
            # A lone <p> is not a parseable document for trafilatura, so the
            # feed body is wrapped the way a real content:encoded payload
            # arrives — otherwise this fixture would silently exercise the
            # refetch path instead of the feed-content path it is testing.
            #
            # Worth knowing when reading the phase-1 exit measurement: this is
            # not just a fixture artifact. A real single-paragraph
            # content:encoded payload also extracts to 0 words and so falls
            # through to the refetch branch. That is benign — refetch is the
            # designed fallback and recovers the full page — but it means short
            # one-paragraph posts inflate the refetch rate for a reason
            # unrelated to feed truncation, which is what that rate is meant to
            # measure when deciding whether per-site extraction overrides are
            # needed.
            summary_html="<html><body><p>" + ("word " * 200) + "</p></body></html>",
            published_at=NOW,
        )
        for i in range(n)
    ]


async def _pipeline(store, driver, summarizer, bus=None):
    registry = SourceRegistry()
    registry.register(driver)
    return Pipeline(
        store=store, registry=registry, summarizer=summarizer,
        bus=bus or EventBus(),
    )


async def test_processes_source_end_to_end(store: StateStore) -> None:
    await store.upsert_source(CFG)
    driver, summarizer = StubDriver(_refs(2)), StubSummarizer()
    pipeline = await _pipeline(store, driver, summarizer)

    result = await pipeline.process_source(CFG, PollState(), now=NOW)

    assert result.new_articles == 2
    assert summarizer.calls == 2

    cur = await store.db.execute("SELECT COUNT(*) AS n FROM summaries")
    assert (await cur.fetchone())["n"] == 2


async def test_rerun_is_idempotent(store: StateStore) -> None:
    """Second pass must not re-summarize — the checkpoint guards it."""
    await store.upsert_source(CFG)
    driver, summarizer = StubDriver(_refs(2)), StubSummarizer()
    pipeline = await _pipeline(store, driver, summarizer)

    await pipeline.process_source(CFG, PollState(), now=NOW)
    await pipeline.process_source(CFG, PollState(), now=NOW)

    assert summarizer.calls == 2, "summarizer ran again on unchanged articles"


async def test_one_failing_article_does_not_abort_the_batch(
    store: StateStore,
) -> None:
    await store.upsert_source(CFG)
    driver = StubDriver(_refs(3))
    pipeline = await _pipeline(store, driver, StubSummarizer(fail=True))

    result = await pipeline.process_source(CFG, PollState(), now=NOW)

    assert result.new_articles == 3
    assert result.failed == 3

    cur = await store.db.execute(
        "SELECT COUNT(*) AS n FROM articles WHERE error_stage = 'summarize'"
    )
    assert (await cur.fetchone())["n"] == 3


async def test_summarize_failure_preserves_extraction(store: StateStore) -> None:
    """Retry must not re-fetch — extracted_at stays set."""
    await store.upsert_source(CFG)
    pipeline = await _pipeline(store, StubDriver(_refs(1)), StubSummarizer(fail=True))
    await pipeline.process_source(CFG, PollState(), now=NOW)

    cur = await store.db.execute("SELECT extracted_at, summarized_at FROM articles")
    row = await cur.fetchone()
    assert row["extracted_at"] is not None
    assert row["summarized_at"] is None


async def test_feed_content_avoids_refetch(store: StateStore) -> None:
    """Substack ships full text in the feed, so fetch_html should not be called."""
    await store.upsert_source(CFG)
    driver = StubDriver(_refs(1))
    pipeline = await _pipeline(store, driver, StubSummarizer())

    await pipeline.process_source(CFG, PollState(), now=NOW)

    assert driver.fetch_calls == 0


async def test_not_modified_short_circuits(store: StateStore) -> None:
    class NotModifiedDriver(StubDriver):
        async def discover(self, source, state):
            return DiscoverResult(not_modified=True)

    await store.upsert_source(CFG)
    summarizer = StubSummarizer()
    pipeline = await _pipeline(store, NotModifiedDriver([]), summarizer)

    result = await pipeline.process_source(CFG, PollState(), now=NOW)

    assert result.not_modified is True
    assert result.new_articles == 0
    assert summarizer.calls == 0


async def test_stranded_article_is_recovered_on_a_later_poll(
    store: StateStore,
) -> None:
    """A crash between ingest and extract must not strand the article forever.

    The row already exists, so ``ingest_article`` returns None and it never
    re-enters ``new_ids``; and ``articles_pending("summarize")`` skips it
    because ``extracted_at`` is NULL. Only the extract-pending union recovers
    it. Without that union the article is silently lost — no error, no log, no
    retry — which is exactly the "no missed posts" failure phase 1 must catch.

    The stranded article is deliberately absent from this poll's refs, which is
    the real-world case (it was discovered on an earlier poll) and forces
    ``_extract``'s ``fetch_ref`` fallback — the branch the happy-path tests
    never touch.
    """
    await store.upsert_source(CFG)
    stranded = _refs(1)[0]
    await store.ingest_article(stranded, canonicalize_url(stranded.url), NOW)

    page = (
        "<html><body><article>"
        + "".join(
            f"<p>Paragraph {i} of the refetched page, carrying enough distinct "
            f"prose that the extractor scores it as real content.</p>"
            for i in range(20)
        )
        + "</article></body></html>"
    )
    driver = StubDriver([], html=page)
    summarizer = StubSummarizer()
    pipeline = await _pipeline(store, driver, summarizer)

    result = await pipeline.process_source(CFG, PollState(), now=NOW)

    assert result.new_articles == 0
    assert result.failed == 0
    assert driver.fetch_calls == 1, "recovery must refetch: no ref in this poll"
    assert summarizer.calls == 1

    cur = await store.db.execute("SELECT extracted_at, summarized_at FROM articles")
    row = await cur.fetchone()
    assert row["extracted_at"] is not None
    assert row["summarized_at"] is not None


async def test_recovery_does_not_adopt_another_sources_articles(
    store: StateStore,
) -> None:
    """Polling one source must never process another source's pending work.

    ``articles_pending`` is global unless scoped, so an unscoped recovery would
    fetch the other source's article with *this* driver and publish it carrying
    *this* source's ref — the live feed would show the post under the wrong
    publication until a reload re-read the correct source_id from the row.
    Asserting on the event payload matters: the row keeps the right source_id
    either way, so row counts alone cannot catch the mislabel.
    """
    other = SourceConfig(
        id="other", type="stub", name="Other", feed_url="https://y/feed"
    )
    await store.upsert_source(CFG)
    await store.upsert_source(other)

    foreign = ArticleRef(
        source_id="other", guid="b0", url="https://other.example/p/9",
        title="Other Post", published_at=NOW,
    )
    await store.ingest_article(foreign, canonicalize_url(foreign.url), NOW)

    seen: list[Event] = []

    async def collect(event: Event) -> None:
        seen.append(event)

    bus = EventBus()
    bus.subscribe(EventType.ARTICLE_SUMMARIZED, collect)

    driver = StubDriver(_refs(1))
    summarizer = StubSummarizer()
    pipeline = await _pipeline(store, driver, summarizer, bus)

    await pipeline.process_source(CFG, PollState(), now=NOW)

    # The other source's article is untouched: not fetched, not summarized.
    assert driver.fetch_calls == 0
    assert summarizer.calls == 1, "only this source's article may be summarized"

    cur = await store.db.execute(
        "SELECT extracted_at, summarized_at FROM articles WHERE source_id = 'other'"
    )
    row = await cur.fetchone()
    assert row["extracted_at"] is None
    assert row["summarized_at"] is None

    # No event may attribute a foreign article to the polled source.
    labels = [(e.data["item"]["url"], e.data["item"]["source"]["id"]) for e in seen]
    assert labels == [("https://acx.substack.com/p/0", "acx")]
    assert all(url != foreign.url for url, _ in labels)


async def test_publishes_events(store: StateStore) -> None:
    seen: list[Event] = []

    async def collect(event: Event) -> None:
        seen.append(event)

    bus = EventBus()
    bus.subscribe(EventType.ARTICLE_INGESTED, collect)
    bus.subscribe(EventType.ARTICLE_SUMMARIZED, collect)

    await store.upsert_source(CFG)
    pipeline = await _pipeline(store, StubDriver(_refs(1)), StubSummarizer(), bus)
    await pipeline.process_source(CFG, PollState(), now=NOW)

    kinds = [e.type for e in seen]
    assert EventType.ARTICLE_INGESTED in kinds
    assert EventType.ARTICLE_SUMMARIZED in kinds
