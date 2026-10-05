"""Smoke tests for the HTTP + WebSocket surface.

These use FastAPI's ``TestClient`` against a hermetic app (no static mount,
no real lifespan) so they run anywhere without configuration.
"""

from __future__ import annotations

import json

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


class _RecordingConnection:
    """Stand-in for a ``WebSocket`` that records what was sent to it."""

    def __init__(self) -> None:
        self.accepted = False
        self.sent: list[str] = []

    async def accept(self) -> None:
        self.accepted = True

    async def send_text(self, payload: str) -> None:
        self.sent.append(payload)


async def test_websocket_receives_broadcast_events(
    bus: EventBus, ws_manager: WebSocketManager
) -> None:
    """An event published while a client is connected reaches that client."""

    connection = _RecordingConnection()
    await ws_manager.connect(connection)  # type: ignore[arg-type]
    assert connection.accepted

    await bus.publish(
        Event(
            type=EventType.ARTICLE_SUMMARIZED,
            subject="42",
            data={"item": {"article_id": 42}},
            source="test",
        )
    )

    assert len(connection.sent) == 1
    received = json.loads(connection.sent[0])
    assert received["type"] == EventType.ARTICLE_SUMMARIZED.value
    assert received["subject"] == "42"
    assert received["data"] == {"item": {"article_id": 42}}


def test_websocket_endpoint_accepts_connections(
    client: TestClient, ws_manager: WebSocketManager
) -> None:
    """The /ws route upgrades and registers the client with the manager."""

    with client.websocket_connect("/api/ws"):
        assert ws_manager.active_count == 1


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
