"""Dismissed articles: swiped out of the feed, kept in Saved, undoable."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from core.api import create_app
from core.events import EventBus
from core.state import StateStore
from core.websocket import WebSocketManager
from schemas.article import ArticleRef, Summary
from schemas.scoring import Score
from schemas.source import SourceConfig

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
CFG = SourceConfig(id="acx", type="substack", name="ACX", feed_url="https://x/feed")


async def _seed(store: StateStore, plan: list[tuple[str, int | None]]) -> dict[str, int]:
    """(headline, score or None) -> a summarized, optionally scored article id."""
    await store.upsert_source(CFG)
    ids = {}
    for i, (headline, score) in enumerate(plan):
        ref = ArticleRef(source_id="acx", guid=f"g{i}", url=f"https://x/{i}",
                         title=headline, published_at=NOW - timedelta(hours=i))
        article_id = await store.ingest_article(ref, ref.url, NOW)
        await store.save_extraction(article_id, "text", 500, "<p/>", NOW)
        await store.save_summary(
            article_id,
            Summary(headline=headline, bullets=["b"] * 5, model="m", prompt_version="v", categories=["ai"]),
            summarized_at=NOW,
        )
        if score is not None:
            await store.save_score(
                article_id,
                Score(value=score, rationale="r", rubric_version="r1", profile_version="p1"),
                NOW,
            )
        ids[headline] = article_id
    return ids


def _headlines(rows: list[dict]) -> list[str]:
    return [r["headline"] for r in rows]


async def test_dismissed_articles_leave_the_ranked_feed_entirely(store: StateStore) -> None:
    ids = await _seed(store, [("keep", 80), ("read", 70), ("low", 10), ("unscored", None)])
    for name in ("read", "low", "unscored"):
        assert await store.dismiss_article(ids[name], NOW) is True
    ranked = await store.ranked_items(cutoff=50, limit=10)
    assert _headlines(ranked["displayed"]) == ["keep"]
    # Not tucked into the "below the cutoff" or "unscored" groups either.
    assert ranked["below_cutoff"]["count"] == 0
    assert ranked["unscored"]["count"] == 0
    assert ranked["total"] == 1


async def test_dismissed_articles_leave_the_plain_feed_but_stay_saved(store: StateStore) -> None:
    ids = await _seed(store, [("a", 80), ("b", 70)])
    await store.save_article(ids["a"], NOW)
    await store.dismiss_article(ids["a"], NOW)
    assert _headlines(await store.feed_items()) == ["b"]
    assert _headlines(await store.feed_items(saved_only=True)) == ["a"]


async def test_undo_brings_it_back(store: StateStore) -> None:
    ids = await _seed(store, [("a", 80)])
    await store.dismiss_article(ids["a"], NOW)
    assert await store.undismiss_article(ids["a"]) is True
    assert _headlines((await store.ranked_items(cutoff=0, limit=10))["displayed"]) == ["a"]


async def test_unknown_article_is_reported(store: StateStore) -> None:
    assert await store.dismiss_article(999, NOW) is False
    assert await store.undismiss_article(999) is False


async def test_dismiss_endpoints(store: StateStore) -> None:
    ids = await _seed(store, [("a", 80), ("b", 70)])
    bus = EventBus()
    ws = WebSocketManager()
    ws.subscribe_to_bus(bus)
    client = TestClient(create_app(
        event_bus=bus, ws_manager=ws, state_store=store, poller=None, settings=None, mount_static=False,
    ))
    assert client.post(f"/api/articles/{ids['a']}/dismiss").status_code == 204
    assert client.post(f"/api/articles/{ids['a']}/dismiss").status_code == 204  # idempotent
    assert [i["headline"] for i in client.get("/api/feed/").json()] == ["b"]
    assert client.delete(f"/api/articles/{ids['a']}/dismiss").status_code == 204
    assert {i["headline"] for i in client.get("/api/feed/").json()} == {"a", "b"}
    assert client.post("/api/articles/999/dismiss").status_code == 404


async def test_filter_counts_skip_dismissed_but_saved_counts_keep_them(store: StateStore) -> None:
    ids = await _seed(store, [("a", 80), ("b", 70)])
    await store.save_article(ids["a"], NOW)
    await store.dismiss_article(ids["a"], NOW)
    assert (await store.category_counts(["ai"]))["ai"] == 1
    assert (await store.category_counts(["ai"], saved_only=True))["ai"] == 1
