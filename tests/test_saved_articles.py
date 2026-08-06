"""Saved articles: persistence, idempotency, filtering, scoped counts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.state import StateStore
from schemas.article import ArticleRef, Summary
from schemas.source import SourceConfig

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(hours=1)
CFG = SourceConfig(id="acx", type="substack", name="ACX", feed_url="https://x/feed")


async def _seed(store: StateStore, tagged: dict[str, list[str]]) -> dict[str, int]:
    """One summarized article per key, tagged with its value."""
    await store.upsert_source(CFG)
    ids: dict[str, int] = {}
    for i, (name, cats) in enumerate(tagged.items()):
        ref = ArticleRef(source_id="acx", guid=f"g{i}",
                         url=f"https://acx.example/p/{i}", title=name,
                         published_at=NOW)
        article_id = await store.ingest_article(ref, ref.url, NOW)
        await store.save_extraction(article_id, "text", 1, "<p/>", NOW)
        await store.save_summary(
            article_id,
            Summary(headline=name, bullets=["b"] * 5, model="m",
                    prompt_version="summary-v2", categories=cats),
            summarized_at=NOW,
        )
        ids[name] = article_id
    return ids


async def test_saving_marks_the_article(store: StateStore) -> None:
    ids = await _seed(store, {"a": ["ai"]})
    assert await store.save_article(ids["a"], NOW) is True
    assert (await store.feed_items())[0]["saved"] is True


async def test_unsaved_articles_report_false(store: StateStore) -> None:
    await _seed(store, {"a": ["ai"]})
    assert (await store.feed_items())[0]["saved"] is False


async def test_unsaving_clears_the_mark(store: StateStore) -> None:
    ids = await _seed(store, {"a": ["ai"]})
    await store.save_article(ids["a"], NOW)
    assert await store.unsave_article(ids["a"]) is True
    assert (await store.feed_items())[0]["saved"] is False


async def test_saving_twice_does_not_refresh_the_timestamp(store: StateStore) -> None:
    """Re-starring must not silently reorder the Saved list."""
    ids = await _seed(store, {"a": ["ai"]})
    await store.save_article(ids["a"], NOW)
    await store.save_article(ids["a"], LATER)

    cur = await store.db.execute(
        "SELECT saved_at FROM articles WHERE id = ?", (ids["a"],)
    )
    assert (await cur.fetchone())["saved_at"] == "2026-08-05T12:00:00+00:00"


async def test_unsaving_an_unsaved_article_succeeds(store: StateStore) -> None:
    """Idempotent: nothing to do is not an error."""
    ids = await _seed(store, {"a": ["ai"]})
    assert await store.unsave_article(ids["a"]) is True


async def test_saving_an_already_saved_article_succeeds(store: StateStore) -> None:
    """True means 'the article exists', never 'the state changed'.

    Added to close a gap found reviewing Task 2: the unsave mirror of this
    was pinned, but the save case — the one the plan calls the most likely
    place to get subtly wrong — was only covered at the API layer.
    """
    ids = await _seed(store, {"a": ["ai"]})
    await store.save_article(ids["a"], NOW)
    assert await store.save_article(ids["a"], LATER) is True


async def test_unknown_article_returns_false(store: StateStore) -> None:
    """False means 'no such article' — never 'no change was needed'."""
    await _seed(store, {"a": ["ai"]})
    assert await store.save_article(9999, NOW) is False
    assert await store.unsave_article(9999) is False


async def test_deleting_an_article_drops_its_saved_state(store: StateStore) -> None:
    ids = await _seed(store, {"a": ["ai"]})
    await store.save_article(ids["a"], NOW)
    await store.db.execute("DELETE FROM articles WHERE id = ?", (ids["a"],))
    await store.db.commit()

    cur = await store.db.execute("SELECT COUNT(*) AS n FROM articles")
    assert (await cur.fetchone())["n"] == 0


async def test_saved_only_returns_just_saved_articles(store: StateStore) -> None:
    ids = await _seed(store, {"kept": ["ai"], "skipped": ["ai"]})
    await store.save_article(ids["kept"], NOW)

    rows = await store.feed_items(saved_only=True)
    assert [r["headline"] for r in rows] == ["kept"]


async def test_saved_only_orders_by_most_recently_saved(store: StateStore) -> None:
    """Newest-saved first, regardless of publication date."""
    ids = await _seed(store, {"first": ["ai"], "second": ["ai"]})
    await store.save_article(ids["first"], NOW)
    await store.save_article(ids["second"], LATER)

    rows = await store.feed_items(saved_only=True)
    assert [r["headline"] for r in rows] == ["second", "first"]


async def test_saved_only_composes_with_category_filter(store: StateStore) -> None:
    ids = await _seed(store, {
        "both": ["ai", "finance"], "ai_only": ["ai"], "unsaved": ["ai", "finance"],
    })
    await store.save_article(ids["both"], NOW)
    await store.save_article(ids["ai_only"], NOW)

    rows = await store.feed_items(categories=["ai", "finance"], saved_only=True)
    assert [r["headline"] for r in rows] == ["both"]


async def test_unfiltered_feed_still_includes_unsaved(store: StateStore) -> None:
    """Saving does not remove an article from the Feed."""
    ids = await _seed(store, {"a": ["ai"], "b": ["ai"]})
    await store.save_article(ids["a"], NOW)
    assert len(await store.feed_items()) == 2


async def test_counts_scoped_to_saved_differ_from_global(store: StateStore) -> None:
    ids = await _seed(store, {
        "saved_ai": ["ai"], "unsaved_ai": ["ai"], "saved_fin": ["finance"],
    })
    await store.save_article(ids["saved_ai"], NOW)
    await store.save_article(ids["saved_fin"], NOW)

    assert await store.category_counts(["ai", "finance"]) == {"ai": 2, "finance": 1}
    assert await store.category_counts(
        ["ai", "finance"], saved_only=True
    ) == {"ai": 1, "finance": 1}


async def test_scoped_counts_still_backfill_zeros(store: StateStore) -> None:
    """A dead end must render disabled, not vanish — scoped or not."""
    ids = await _seed(store, {"a": ["ai"]})
    await store.save_article(ids["a"], NOW)

    counts = await store.category_counts(["ai", "robotics"], saved_only=True)
    assert counts == {"ai": 1, "robotics": 0}


async def test_scoped_counts_are_still_contextual(store: StateStore) -> None:
    ids = await _seed(store, {
        "both": ["ai", "finance"], "ai_only": ["ai"], "unsaved_both": ["ai", "finance"],
    })
    await store.save_article(ids["both"], NOW)
    await store.save_article(ids["ai_only"], NOW)

    counts = await store.category_counts(
        ["ai", "finance"], selected=["ai"], saved_only=True
    )
    assert counts == {"ai": 2, "finance": 1}
