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

VOCABULARY = ["ai", "finance", "robotics", "semiconductors"]
FILTERS = ["ai", "finance", "robotics"]

# Article index -> its categories. Index 2 is summarized but untagged, so a
# filtered feed is strictly smaller than the unfiltered one; index 3 is never
# summarized and must stay out of both.
CATEGORIES = {0: ["ai", "finance"], 1: ["ai"], 2: []}


@pytest.fixture
async def seeded(store: StateStore) -> StateStore:
    await store.upsert_source(CFG)
    for i in range(4):
        ref = ArticleRef(
            source_id="acx", guid=f"g{i}",
            url=f"https://acx.substack.com/p/{i}", title=f"Post {i}",
            published_at=datetime(2026, 8, i + 1, tzinfo=timezone.utc),
        )
        article_id = await store.ingest_article(ref, ref.url, NOW)
        await store.save_extraction(article_id, "text", 1, "<p/>", NOW)
        if i in CATEGORIES:  # leave one unsummarized
            await store.save_summary(
                article_id,
                Summary(headline=f"Headline {i}", bullets=[f"b{i}"] * 5,
                        model="m", prompt_version="v1",
                        categories=CATEGORIES[i]),
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
        category_vocabulary=VOCABULARY, category_filters=FILTERS,
    )
    return TestClient(app)


def test_feed_returns_only_summarized_articles(client: TestClient) -> None:
    res = client.get("/feed/")
    assert res.status_code == 200

    items = res.json()
    assert len(items) == 3, "the unsummarized article must not appear"


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


def test_manual_poll_returns_503_when_poller_absent(client: TestClient) -> None:
    """No poller on app.state means no manual poll.

    ``main.py`` deliberately exposes ``app.state.poller = None`` when the
    poller never started (no ANTHROPIC_API_KEY), because the pipeline behind
    it has no summarizer. This route must fail cleanly rather than drive it.
    """

    res = client.post("/sources/acx/poll")

    assert res.status_code == 503
    assert "poller" in res.json()["detail"]


def test_manual_poll_checks_poller_before_source_exists(
    client: TestClient,
) -> None:
    """503 wins over 404 — the service is down, the lookup is moot."""

    assert client.post("/sources/nope/poll").status_code == 503


def test_categories_endpoint_lists_configured_filters(client) -> None:
    body = client.get("/categories/").json()
    assert [f["category"] for f in body["filters"]] == ["ai", "finance", "robotics"]
    assert body["selected"] == []


def test_categories_counts_are_contextual(client) -> None:
    body = client.get("/categories/?category=ai").json()
    counts = {f["category"]: f["count"] for f in body["filters"]}
    assert counts["finance"] == 1
    assert counts["robotics"] == 0
    assert body["selected"] == ["ai"]
    assert next(f for f in body["filters"] if f["category"] == "ai")["selected"]


def test_feed_filters_with_and_semantics(client) -> None:
    both = client.get("/feed/?category=ai&category=finance").json()
    assert len(both) == 1
    ai_only = client.get("/feed/?category=ai").json()
    assert len(ai_only) == 2


def test_feed_filter_excludes_untagged(client) -> None:
    unfiltered = client.get("/feed/").json()
    filtered = client.get("/feed/?category=ai").json()
    assert len(unfiltered) > len(filtered)


def test_unknown_category_returns_400(client) -> None:
    res = client.get("/feed/?category=nonsense")
    assert res.status_code == 400
    assert "nonsense" in res.json()["detail"]


def test_vocabulary_category_not_in_filters_is_accepted(client) -> None:
    """filters controls the UI only; the data exists for the vocabulary."""
    assert client.get("/feed/?category=semiconductors").status_code == 200


def test_duplicate_category_params_collapse(client) -> None:
    """?category=AI&category=ai is one filter, not an unsatisfiable two."""
    assert len(client.get("/feed/?category=AI&category=ai").json()) == 2


def test_feed_items_carry_categories(client) -> None:
    item = client.get("/feed/?category=ai&category=finance").json()[0]
    assert sorted(item["categories"]) == ["ai", "finance"]


def test_category_filter_composes_with_limit(client) -> None:
    assert len(client.get("/feed/?category=ai&limit=1").json()) == 1
