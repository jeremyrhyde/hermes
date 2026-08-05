"""Category persistence, AND filtering, and contextual counts."""

from __future__ import annotations

from datetime import datetime, timezone

from core.state import StateStore
from schemas.article import ArticleRef, Summary
from schemas.source import SourceConfig

NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
CFG = SourceConfig(id="acx", type="substack", name="ACX", feed_url="https://x/feed")


async def _seed(store: StateStore, tagged: dict[str, list[str]]) -> dict[str, int]:
    """Create one summarized article per key, tagged with its value."""
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


async def test_categories_persist_and_read_back(store: StateStore) -> None:
    await _seed(store, {"both": ["ai", "finance"]})
    rows = await store.feed_items()
    assert sorted(rows[0]["categories"]) == ["ai", "finance"]


async def test_resummarizing_replaces_rather_than_accumulates(store: StateStore) -> None:
    ids = await _seed(store, {"a": ["ai", "finance"]})
    await store.save_summary(
        ids["a"],
        Summary(headline="a", bullets=["b"] * 5, model="m",
                prompt_version="summary-v2", categories=["robotics"]),
        summarized_at=NOW,
    )
    cur = await store.db.execute("SELECT COUNT(*) AS n FROM article_categories")
    assert (await cur.fetchone())["n"] == 1
    assert (await store.feed_items())[0]["categories"] == ["robotics"]


async def test_no_filter_returns_everything_including_untagged(store: StateStore) -> None:
    """No backfill: untagged articles live in the unfiltered feed."""
    await _seed(store, {"ai_only": ["ai"], "untagged": []})
    assert len(await store.feed_items()) == 2


async def test_single_filter_excludes_untagged(store: StateStore) -> None:
    await _seed(store, {"ai_only": ["ai"], "untagged": []})
    rows = await store.feed_items(categories=["ai"])
    assert [r["headline"] for r in rows] == ["ai_only"]


async def test_two_filters_are_AND_not_OR(store: StateStore) -> None:
    """The whole point: compounding narrows."""
    await _seed(store, {
        "both": ["ai", "finance"],
        "ai_only": ["ai"],
        "finance_only": ["finance"],
    })
    rows = await store.feed_items(categories=["ai", "finance"])
    assert [r["headline"] for r in rows] == ["both"]


async def test_three_filters_narrow_further(store: StateStore) -> None:
    await _seed(store, {
        "all_three": ["ai", "finance", "robotics"],
        "two_of_three": ["ai", "finance"],
    })
    rows = await store.feed_items(categories=["ai", "finance", "robotics"])
    assert [r["headline"] for r in rows] == ["all_three"]


async def test_impossible_combination_returns_empty(store: StateStore) -> None:
    await _seed(store, {"ai_only": ["ai"], "finance_only": ["finance"]})
    assert await store.feed_items(categories=["ai", "finance"]) == []


async def test_counts_are_global_with_no_selection(store: StateStore) -> None:
    await _seed(store, {
        "both": ["ai", "finance"], "ai_only": ["ai"], "untagged": [],
    })
    counts = await store.category_counts(["ai", "finance", "robotics"])
    assert counts == {"ai": 2, "finance": 1, "robotics": 0}


async def test_counts_are_contextual_once_a_filter_is_selected(store: StateStore) -> None:
    """finance drops to 1 given ai; robotics becomes a visible dead end."""
    await _seed(store, {
        "both": ["ai", "finance"],
        "ai_only": ["ai"],
        "finance_only": ["finance"],
        "robotics_only": ["robotics"],
    })
    counts = await store.category_counts(
        ["ai", "finance", "robotics"], selected=["ai"]
    )
    assert counts == {"ai": 2, "finance": 1, "robotics": 0}


async def test_counts_include_zero_entries(store: StateStore) -> None:
    """A dead end must be reported, not omitted — else the button vanishes."""
    await _seed(store, {"ai_only": ["ai"]})
    counts = await store.category_counts(["ai", "finance"], selected=["ai"])
    assert counts["finance"] == 0
    assert set(counts) == {"ai", "finance"}
