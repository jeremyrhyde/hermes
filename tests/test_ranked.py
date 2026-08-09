"""Gating math: boundaries, ties, truncation, and the unscored group."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.state import StateStore
from schemas.article import ArticleRef, Summary
from schemas.scoring import Score
from schemas.source import SourceConfig

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
CFG = SourceConfig(id="acx", type="substack", name="ACX", feed_url="https://x/feed")


async def _seed(store: StateStore, plan: list[tuple[str, int | None, int]],
                categories: list[str] | None = None) -> None:
    """(headline, score or None, hours_old) -> a summarized, optionally scored article."""
    await store.upsert_source(CFG)
    for headline, score, age in plan:
        guid = headline.replace(" ", "-")
        ref = ArticleRef(source_id="acx", guid=guid, url=f"https://x/{guid}",
                         title=headline, published_at=NOW - timedelta(hours=age))
        article_id = await store.ingest_article(ref, ref.url, NOW)
        await store.save_extraction(article_id, "text", 500, "<p/>", NOW)
        await store.save_summary(
            article_id,
            Summary(headline=headline, bullets=["b"] * 5, model="m",
                    prompt_version="summary-v3",
                    categories=categories if categories is not None else ["ai"]),
            summarized_at=NOW,
        )
        if score is not None:
            await store.save_score(
                article_id,
                Score(value=score, rationale="r", rubric_version="rubric-v1",
                      profile_version="profile-v1"),
                NOW,
            )


async def test_displayed_is_ordered_by_score(store: StateStore) -> None:
    await _seed(store, [("low", 20, 1), ("high", 90, 2), ("mid", 55, 3)])

    result = await store.ranked_items(cutoff=0, limit=10)

    assert [r["headline"] for r in result["displayed"]] == ["high", "mid", "low"]


async def test_ties_break_on_recency(store: StateStore) -> None:
    await _seed(store, [("older", 70, 5), ("newer", 70, 1)])

    result = await store.ranked_items(cutoff=0, limit=10)

    assert [r["headline"] for r in result["displayed"]] == ["newer", "older"]


async def test_a_score_exactly_at_the_cutoff_is_above_it(store: StateStore) -> None:
    await _seed(store, [("boundary", 70, 1)])

    result = await store.ranked_items(cutoff=70, limit=10)

    assert [r["headline"] for r in result["displayed"]] == ["boundary"]
    assert result["below_cutoff"]["count"] == 0


async def test_below_cutoff_is_counted_with_its_range(store: StateStore) -> None:
    await _seed(store, [("a", 90, 1), ("b", 40, 2), ("c", 31, 3)])

    result = await store.ranked_items(cutoff=70, limit=10)

    assert [r["headline"] for r in result["displayed"]] == ["a"]
    assert result["below_cutoff"] == {"count": 2, "high": 40, "low": 31}


async def test_max_displayed_pushes_the_rest_into_above_cutoff(store: StateStore) -> None:
    """above_cutoff is overflow, not a superset of displayed."""
    await _seed(store, [("a", 95, 1), ("b", 88, 2), ("c", 80, 3), ("d", 74, 4)])

    result = await store.ranked_items(cutoff=70, limit=2)

    assert [r["headline"] for r in result["displayed"]] == ["a", "b"]
    assert result["above_cutoff"] == {"count": 2, "high": 80, "low": 74}


async def test_unscored_articles_are_counted_separately(store: StateStore) -> None:
    """Ranking them by NULL is how they vanish silently."""
    await _seed(store, [("scored", 90, 1), ("pending", None, 2)])

    result = await store.ranked_items(cutoff=0, limit=10)

    assert [r["headline"] for r in result["displayed"]] == ["scored"]
    assert result["unscored"] == {"count": 1}


async def test_everything_unscored_yields_an_empty_but_honest_view(
    store: StateStore,
) -> None:
    """The state of the feed for one poll cycle after deploy."""
    await _seed(store, [("a", None, 1), ("b", None, 2)])

    result = await store.ranked_items(cutoff=0, limit=10)

    assert result["displayed"] == []
    assert result["unscored"]["count"] == 2
    assert result["total"] == 2


async def test_empty_groups_report_null_ranges_not_missing_keys(
    store: StateStore,
) -> None:
    await _seed(store, [("only", 90, 1)])

    result = await store.ranked_items(cutoff=0, limit=10)

    assert result["above_cutoff"] == {"count": 0, "high": None, "low": None}
    assert result["below_cutoff"] == {"count": 0, "high": None, "low": None}


async def test_total_equals_the_sum_of_every_group(store: StateStore) -> None:
    """What makes 'showing 5 of 30' true rather than merely plausible."""
    await _seed(store, [("a", 95, 1), ("b", 88, 2), ("c", 40, 3), ("d", None, 4)])

    result = await store.ranked_items(cutoff=70, limit=1)

    assert result["total"] == (
        len(result["displayed"])
        + result["above_cutoff"]["count"]
        + result["below_cutoff"]["count"]
        + result["unscored"]["count"]
    )
    assert result["total"] == 4


async def test_category_filter_narrows_every_group(store: StateStore) -> None:
    await _seed(store, [("ai one", 90, 1)], categories=["ai"])
    await _seed(store, [("finance one", 80, 2)], categories=["finance"])

    result = await store.ranked_items(cutoff=0, limit=10, categories=["finance"])

    assert [r["headline"] for r in result["displayed"]] == ["finance one"]
    assert result["total"] == 1


async def test_unusable_articles_are_excluded(store: StateStore) -> None:
    await _seed(store, [("good", 90, 1)])
    await _seed(store, [("bad", None, 2)])
    cur = await store.db.execute("SELECT id FROM articles WHERE title = 'bad'")
    await store.mark_unusable((await cur.fetchone())["id"], "paywalled", NOW)

    result = await store.ranked_items(cutoff=0, limit=10)

    assert result["total"] == 1
