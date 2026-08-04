"""Feed API: FeedItem assembly, source health, manual poll trigger."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from core.api import create_app
from core.events import EventBus
from core.state import StateStore
from core.websocket import WebSocketManager
from schemas.article import ArticleRef, Summary
from schemas.source import SourceConfig

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
CFG = SourceConfig(id="acx", type="substack", name="Astral Codex Ten",
                   feed_url="https://x/feed")


@pytest.fixture
async def seeded(store: StateStore) -> StateStore:
    await store.upsert_source(CFG)
    for i in range(3):
        ref = ArticleRef(
            source_id="acx", guid=f"g{i}",
            url=f"https://acx.substack.com/p/{i}", title=f"Post {i}",
            published_at=datetime(2026, 8, i + 1, tzinfo=timezone.utc),
        )
        article_id = await store.ingest_article(ref, ref.url, NOW)
        await store.save_extraction(article_id, "text", 1, "<p/>", NOW)
        if i < 2:  # leave one unsummarized
            await store.save_summary(
                article_id,
                Summary(headline=f"Headline {i}", bullets=[f"b{i}"] * 5,
                        model="m", prompt_version="v1"),
                summarized_at=NOW,
            )
    return store


@pytest.fixture
def client(seeded: StateStore) -> TestClient:
    bus = EventBus()
    ws = WebSocketManager()
    ws.subscribe_to_bus(bus)
    app = create_app(
        event_bus=bus, ws_manager=ws, state_store=seeded,
        poller=None, settings=None, mount_static=False,
    )
    return TestClient(app)


def test_feed_returns_only_summarized_articles(client: TestClient) -> None:
    res = client.get("/feed/")
    assert res.status_code == 200

    items = res.json()
    assert len(items) == 2, "the unsummarized article must not appear"


def test_feed_items_carry_source_ref(client: TestClient) -> None:
    """Spec 12.5: source identity ships in the card."""
    item = client.get("/feed/").json()[0]

    assert item["source"]["id"] == "acx"
    assert item["source"]["name"] == "Astral Codex Ten"
    assert item["source"]["type"] == "substack"


def test_feed_is_reverse_chronological(client: TestClient) -> None:
    items = client.get("/feed/").json()
    assert items[0]["published_at"] > items[1]["published_at"]


def test_feed_item_shape(client: TestClient) -> None:
    item = client.get("/feed/").json()[0]

    assert set(item) >= {
        "article_id", "headline", "bullets", "url", "published_at",
        "source", "score", "rating", "badges",
    }
    assert len(item["bullets"]) == 5
    assert item["score"] is None, "phase 1 has no scoring"


def test_feed_respects_limit(client: TestClient) -> None:
    assert len(client.get("/feed/?limit=1").json()) == 1


def test_sources_endpoint_reports_health(client: TestClient) -> None:
    rows = client.get("/sources/").json()

    assert len(rows) == 1
    assert rows[0]["id"] == "acx"
    assert rows[0]["enabled"] is True
    assert rows[0]["error_count"] == 0
    assert rows[0]["disabled"] is False
