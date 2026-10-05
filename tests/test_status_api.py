import httpx

from config import Settings
from core.api import create_app
from core.events import EventBus
from core.websocket import WebSocketManager
from services.usage import ClaudeUsage


def make_app(store, key: str):
    app = create_app(event_bus=EventBus(), ws_manager=WebSocketManager(), state_store=store,
                     poller=None, settings=Settings(_env_file=None, ANTHROPIC_API_KEY=key),
                     mount_static=False)
    usage = ClaudeUsage(clock=lambda: 1000.0)
    usage.record(True)
    usage.record(False)
    app.state.claude_usage = usage
    app.state.startup_failures = []
    return app


async def get_status(app) -> dict:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        res = await c.get("/api/status")
    assert res.status_code == 200
    return res.json()


async def test_status_ok_with_usage(store):
    body = await get_status(make_app(store, "k"))
    assert body["state"] == "ok"
    stats = {s["label"]: s for s in body["stats"]}
    assert stats["Claude requests (24h)"]["value"] == 2
    assert stats["Claude errors (24h)"] == {"label": "Claude errors (24h)", "value": 1, "kind": "count", "warn": True}
    assert stats["Last Claude call"]["kind"] == "time"
    assert stats["Sources failing"]["value"] == 0


async def test_status_degraded_without_api_key(store):
    body = await get_status(make_app(store, ""))
    assert body["state"] == "degraded" and "API key" in body["summary"]


async def _seed_source(store, error_count: int):
    from datetime import datetime, timezone

    from schemas.source import SourceConfig

    cfg = SourceConfig(id="acx", type="substack", name="ACX", feed_url="https://x/feed")
    await store.upsert_source(cfg)
    await store.update_source_poll_state(
        "acx", etag=None, last_modified=None, content_hash=None,
        error_count=error_count, next_poll_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
    )


async def test_status_failing_source_is_degraded(store):
    await _seed_source(store, 3)
    body = await get_status(make_app(store, "k"))
    stats = {s["label"]: s for s in body["stats"]}
    assert stats["Sources failing"]["value"] == 1 and stats["Sources failing"]["warn"] is True
    assert body["state"] == "degraded" and "source(s) failing" in body["summary"]


async def test_status_last_feed_poll_is_stored_value(store):
    await _seed_source(store, 0)
    stored = (await store.all_source_rows())[0]["last_polled_at"]
    assert stored
    body = await get_status(make_app(store, "k"))
    stats = {s["label"]: s for s in body["stats"]}
    assert stats["Last feed poll"]["value"] == stored


async def test_status_advisory_vs_error_startup_failures(store):
    app = make_app(store, "k")
    app.state.startup_failures = [{"component": "scoring", "error": "x", "severity": "advisory"}]
    assert (await get_status(app))["state"] == "ok"
    app.state.startup_failures = [{"component": "summarizer", "error": "x", "severity": "error"}]
    body = await get_status(app)
    assert body["state"] == "degraded" and "startup failure" in body["summary"]


async def test_status_no_key_names_root_cause_once(store):
    app = make_app(store, "")
    app.state.startup_failures = [
        {"component": "summarizer", "error": "no key", "severity": "error"},
        {"component": "scorer", "error": "no key", "severity": "error"},
    ]
    body = await get_status(app)
    assert body["state"] == "degraded"
    assert body["summary"].count("API key") == 1 and "startup failure" not in body["summary"]
