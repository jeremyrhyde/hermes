"""Smoke tests for the HTTP + WebSocket surface.

These use FastAPI's ``TestClient`` against a hermetic app (no static mount,
no real lifespan) so they run anywhere without configuration.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from core.api import create_app
from core.events import EventBus
from core.websocket import WebSocketManager
from schemas.events import Event, EventType


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def ws_manager(bus: EventBus) -> WebSocketManager:
    manager = WebSocketManager()
    manager.subscribe_to_bus(bus)
    return manager


@pytest.fixture
def client(bus: EventBus, ws_manager: WebSocketManager) -> TestClient:
    app = create_app(
        event_bus=bus,
        ws_manager=ws_manager,
        settings=None,
        mount_static=False,
    )
    return TestClient(app)


def test_ping_publishes_an_event(client: TestClient) -> None:
    """POST /events/ping returns the event it put on the bus."""

    res = client.post("/events/ping")
    assert res.status_code == 200

    published = res.json()["published"]
    assert published["type"] == EventType.STATE_CHANGED.value
    assert published["subject"] == "ping"
    assert published["source"] == "api"


def test_websocket_receives_broadcast_events(client: TestClient) -> None:
    """An event published while a client is connected reaches that client."""

    with client.websocket_connect("/ws") as ws:
        client.post("/events/ping")
        received = ws.receive_json()

    assert received["type"] == EventType.STATE_CHANGED.value
    assert received["data"] == {"state": {"pong": True}}


async def test_bus_isolates_failing_subscribers(bus: EventBus) -> None:
    """One raising subscriber must not stop the others from running."""

    seen: list[str] = []

    async def boom(event: Event) -> None:
        raise RuntimeError("subscriber exploded")

    async def record(event: Event) -> None:
        seen.append(event.source)

    bus.subscribe(EventType.SYSTEM_READY, boom)
    bus.subscribe(EventType.SYSTEM_READY, record)

    await bus.publish(Event(type=EventType.SYSTEM_READY, source="test"))

    assert seen == ["test"]


async def test_unsubscribe_is_idempotent(bus: EventBus) -> None:
    """Unsubscribing something never registered is a no-op, not an error."""

    async def never_registered(event: Event) -> None:  # pragma: no cover
        raise AssertionError("should not be called")

    bus.unsubscribe(EventType.SYSTEM_READY, never_registered)
