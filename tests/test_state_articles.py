"""StateStore: source upsert, poll state, idempotent article ingestion."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.state import StateStore
from schemas.article import ArticleRef, Summary
from schemas.source import SourceConfig

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)


async def test_upsert_source_preserves_poll_state(
    store: StateStore, source_config: SourceConfig
) -> None:
    """Re-reading sources.yaml at startup must not wipe etag/hash state."""
    await store.upsert_source(source_config)
    await store.update_source_poll_state(
        "acx", etag='W/"abc"', last_modified="Sat, 02 Aug 2026 10:00:00 GMT",
        content_hash="deadbeef", error_count=0, next_poll_at=NOW,
    )

    await store.upsert_source(source_config)  # restart

    src = await store.get_source_row("acx")
    assert src["etag"] == 'W/"abc"'
    assert src["content_hash"] == "deadbeef"


async def test_ingest_article_is_idempotent(
    store: StateStore, source_config: SourceConfig
) -> None:
    await store.upsert_source(source_config)
    ref = ArticleRef(
        source_id="acx", guid="g1",
        url="https://acx.substack.com/p/one", title="One",
        published_at=NOW,
    )

    first = await store.ingest_article(ref, canonical_url=ref.url, fetched_at=NOW)
    second = await store.ingest_article(ref, canonical_url=ref.url, fetched_at=NOW)

    assert isinstance(first, int)
    assert second is None, "re-ingesting the same guid must be a no-op"

    cur = await store.db.execute("SELECT COUNT(*) AS n FROM articles")
    assert (await cur.fetchone())["n"] == 1


async def test_ingest_dedups_on_canonical_url_across_sources(
    store: StateStore, source_config: SourceConfig
) -> None:
    """Spec section 4: same canonical URL from two sources stores once."""
    await store.upsert_source(source_config)
    await store.upsert_source(
        SourceConfig(id="mirror", type="substack", name="Mirror",
                     feed_url="https://mirror.example/feed")
    )

    a = ArticleRef(source_id="acx", guid="g1",
                   url="https://acx.substack.com/p/one", title="One")
    b = ArticleRef(source_id="mirror", guid="g-other",
                   url="https://acx.substack.com/p/one", title="One (mirror)")

    assert await store.ingest_article(a, a.url, NOW) is not None
    assert await store.ingest_article(b, b.url, NOW) is None


async def test_stage_checkpoints_advance_independently(
    store: StateStore, source_config: SourceConfig
) -> None:
    await store.upsert_source(source_config)
    ref = ArticleRef(source_id="acx", guid="g1",
                     url="https://acx.substack.com/p/one", title="One")
    article_id = await store.ingest_article(ref, ref.url, NOW)

    await store.save_extraction(article_id, text="body text here", word_count=3,
                                raw_html="<p>body</p>", extracted_at=NOW)
    row = await store.get_article_row(article_id)
    assert row["extracted_at"] is not None
    assert row["summarized_at"] is None

    await store.save_summary(
        article_id,
        Summary(headline="H", bullets=["a"] * 5, model="m", prompt_version="v1"),
        summarized_at=NOW,
    )
    row = await store.get_article_row(article_id)
    assert row["summarized_at"] is not None


async def test_record_error_does_not_clear_prior_stage(
    store: StateStore, source_config: SourceConfig
) -> None:
    """A summarization failure must leave extracted_at intact."""
    await store.upsert_source(source_config)
    ref = ArticleRef(source_id="acx", guid="g1",
                     url="https://acx.substack.com/p/one", title="One")
    article_id = await store.ingest_article(ref, ref.url, NOW)
    await store.save_extraction(article_id, "text", 1, "<p/>", NOW)

    await store.record_article_error(article_id, stage="summarize", error="boom")

    row = await store.get_article_row(article_id)
    assert row["extracted_at"] is not None
    assert row["error_stage"] == "summarize"
    assert "boom" in row["last_error"]


async def test_due_sources_respects_next_poll_at(
    store: StateStore, source_config: SourceConfig
) -> None:
    await store.upsert_source(source_config)
    await store.update_source_poll_state(
        "acx", etag=None, last_modified=None, content_hash=None,
        error_count=0, next_poll_at=NOW + timedelta(hours=1),
    )

    assert await store.due_sources(NOW) == []
    assert [s.id for s in await store.due_sources(NOW + timedelta(hours=2))] == ["acx"]


async def test_disabled_source_is_not_due(
    store: StateStore, source_config: SourceConfig
) -> None:
    await store.upsert_source(source_config)
    await store.set_source_disabled_until("acx", NOW + timedelta(hours=4))

    assert await store.due_sources(NOW) == []
    assert [s.id for s in await store.due_sources(NOW + timedelta(hours=5))] == ["acx"]
