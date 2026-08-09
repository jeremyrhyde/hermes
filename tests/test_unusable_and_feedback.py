"""Phase 2: the unusable-extraction gate and feedback capture.

See 2026-08-09-phase2-spec.md. The gate keeps articles whose text we do not
actually have out of the corpus the scorer will learn from; feedback capture
starts accumulating ratings before the scorer exists.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.state import StateStore
from schemas.article import ArticleRef, Summary
from schemas.source import SourceConfig

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(hours=1)
CFG = SourceConfig(id="acx", type="substack", name="ACX", feed_url="https://x/feed")


async def _article(store: StateStore, guid: str = "g0", *, summarize: bool = True,
                   categories: list[str] | None = None) -> int:
    """One extracted article, summarized by default."""
    ref = ArticleRef(source_id="acx", guid=guid,
                     url=f"https://acx.example/p/{guid}", title=f"Post {guid}",
                     published_at=NOW)
    article_id = await store.ingest_article(ref, ref.url, NOW)
    await store.save_extraction(article_id, "text", 500, "<p/>", NOW)
    if summarize:
        await store.save_summary(
            article_id,
            Summary(headline=f"Headline {guid}", bullets=["b"] * 5, model="m",
                    prompt_version="summary-v2",
                    categories=categories if categories is not None else ["ai"]),
            summarized_at=NOW,
        )
    return article_id


# ---------------------------------------------------------------------------
# Unusable state
# ---------------------------------------------------------------------------


async def test_marking_unusable_records_the_reason(store: StateStore) -> None:
    await store.upsert_source(CFG)
    article_id = await _article(store, summarize=False)

    await store.mark_unusable(article_id, "paywalled", NOW)

    cur = await store.db.execute(
        "SELECT unusable_at, unusable_reason FROM articles WHERE id = ?",
        (article_id,),
    )
    row = await cur.fetchone()
    assert row["unusable_at"] is not None
    assert row["unusable_reason"] == "paywalled"


async def test_unusable_article_is_not_pending_summarize(store: StateStore) -> None:
    """THE required exclusion.

    articles_pending("summarize") selects for summarized_at IS NULL, which is
    exactly the state every unusable article is permanently in. Without the
    exclusion each one is handed back on every poll of its source, forever.
    """
    await store.upsert_source(CFG)
    usable = await _article(store, "keep", summarize=False)
    unusable = await _article(store, "drop", summarize=False)

    await store.mark_unusable(unusable, "paywalled", NOW)

    pending = await store.articles_pending("summarize")
    assert usable in pending
    assert unusable not in pending


async def test_unusable_article_is_absent_from_the_feed(store: StateStore) -> None:
    """Defensive: already covered by summarized_at IS NOT NULL (spec 5)."""
    await store.upsert_source(CFG)
    article_id = await _article(store, summarize=False)
    await store.mark_unusable(article_id, "paywalled", NOW)

    assert await store.feed_items() == []


async def test_unusable_article_is_absent_from_the_saved_list(store: StateStore) -> None:
    """Defensive: the saved list shares feed_items' query."""
    await store.upsert_source(CFG)
    article_id = await _article(store, summarize=False)
    await store.mark_unusable(article_id, "paywalled", NOW)
    await store.save_article(article_id, NOW)

    assert await store.feed_items(saved_only=True) == []


async def test_unusable_article_is_absent_from_category_counts(store: StateStore) -> None:
    """Defensive: counts must not describe a population the feed will not show."""
    await store.upsert_source(CFG)
    article_id = await _article(store, categories=["ai"])
    await store.mark_unusable(article_id, "paywalled", NOW)

    counts = await store.category_counts(["ai"])
    assert counts["ai"] == 0


async def test_unusable_counts_group_by_source(store: StateStore) -> None:
    await store.upsert_source(CFG)
    await store.upsert_source(
        SourceConfig(id="pf", type="substack", name="PF", feed_url="https://y/feed")
    )
    a = await _article(store, "a", summarize=False)
    b = await _article(store, "b", summarize=False)
    await store.mark_unusable(a, "paywalled", NOW)
    await store.mark_unusable(b, "thin after refetch (81 words)", NOW)

    assert await store.unusable_counts() == {"acx": 2}, "no key for a source with none"


async def test_unusable_article_keeps_its_text(store: StateStore) -> None:
    """Retention makes a future recovery cheap; it does not make one automatic."""
    await store.upsert_source(CFG)
    article_id = await _article(store, summarize=False)
    await store.mark_unusable(article_id, "paywalled", NOW)

    cur = await store.db.execute(
        "SELECT text, extracted_at FROM articles WHERE id = ?", (article_id,)
    )
    row = await cur.fetchone()
    assert row["text"] == "text" and row["extracted_at"] is not None


# ---------------------------------------------------------------------------
# Ratings
# ---------------------------------------------------------------------------


async def test_rating_is_reported_by_feed_items(store: StateStore) -> None:
    await store.upsert_source(CFG)
    article_id = await _article(store)

    assert await store.rate_article(article_id, 1, NOW) is True
    assert (await store.feed_items())[0]["rating"] == 1


async def test_latest_rating_wins(store: StateStore) -> None:
    """Append-only: changing your mind appends rather than mutating."""
    await store.upsert_source(CFG)
    article_id = await _article(store)

    await store.rate_article(article_id, 1, NOW)
    await store.rate_article(article_id, -1, LATER)

    assert (await store.feed_items())[0]["rating"] == -1
    cur = await store.db.execute(
        "SELECT COUNT(*) c FROM ratings WHERE article_id = ?", (article_id,)
    )
    assert (await cur.fetchone())["c"] == 2, "history is kept, not overwritten"


async def test_clearing_a_rating_returns_it_to_null(store: StateStore) -> None:
    """CHECK (value IN (-1, 1)) makes neutral unrepresentable, so clear deletes."""
    await store.upsert_source(CFG)
    article_id = await _article(store)
    await store.rate_article(article_id, 1, NOW)

    assert await store.clear_rating(article_id) is True
    assert (await store.feed_items())[0]["rating"] is None


async def test_clearing_an_unrated_article_succeeds(store: StateStore) -> None:
    """Existence, not change — matching unsave_article's contract."""
    await store.upsert_source(CFG)
    article_id = await _article(store)

    assert await store.clear_rating(article_id) is True


async def test_rating_an_unknown_article_reports_false(store: StateStore) -> None:
    await store.upsert_source(CFG)
    assert await store.rate_article(9999, 1, NOW) is False
    assert await store.clear_rating(9999) is False


async def test_rating_rejects_a_neutral_value(store: StateStore) -> None:
    """The CHECK constraint is the last line, not the first."""
    await store.upsert_source(CFG)
    article_id = await _article(store)

    with pytest.raises(Exception):
        await store.rate_article(article_id, 0, NOW)


# ---------------------------------------------------------------------------
# Interactions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["expand", "click_through"])
async def test_interactions_persist(store: StateStore, kind: str) -> None:
    await store.upsert_source(CFG)
    article_id = await _article(store)

    assert await store.record_interaction(article_id, kind, NOW) is True

    cur = await store.db.execute(
        "SELECT kind FROM interactions WHERE article_id = ?", (article_id,)
    )
    assert [r["kind"] for r in await cur.fetchall()] == [kind]


async def test_interactions_are_not_deduplicated(store: StateStore) -> None:
    """Expanding the same article three times is signal, not noise."""
    await store.upsert_source(CFG)
    article_id = await _article(store)

    for _ in range(3):
        await store.record_interaction(article_id, "expand", NOW)

    cur = await store.db.execute(
        "SELECT COUNT(*) c FROM interactions WHERE article_id = ?", (article_id,)
    )
    assert (await cur.fetchone())["c"] == 3


async def test_interaction_for_an_unknown_article_reports_false(store: StateStore) -> None:
    await store.upsert_source(CFG)
    assert await store.record_interaction(9999, "expand", NOW) is False
