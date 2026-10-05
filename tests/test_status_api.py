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
