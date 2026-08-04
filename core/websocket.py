"""WebSocket connection manager.

Maintains the set of currently-connected frontend WebSocket clients and
broadcasts :class:`Event` objects to all of them. Plugs into
:class:`core.events.EventBus` via :meth:`subscribe_to_bus` — the API layer
never has to broadcast manually, the bus does it.

Failure isolation
-----------------

A single dead client must never block the others. :meth:`broadcast` fires
every send concurrently via :func:`asyncio.gather` with
``return_exceptions=True``; any connection that raises is dropped from the
active set silently.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import WebSocket

from core.events import EventBus
from schemas.events import Event, EventType

logger = logging.getLogger(__name__)


# Event types pushed to the browser. Add new ones here as the domain grows —
# a type that isn't listed simply never reaches the UI.
BROADCAST_TYPES: tuple[EventType, ...] = (
    EventType.SYSTEM_READY,
    EventType.SOURCE_POLLED,
    EventType.ARTICLE_INGESTED,
    EventType.ARTICLE_SUMMARIZED,
    EventType.ARTICLE_SCORED,
    EventType.PROFILE_PROPOSED,
    EventType.PIPELINE_ERROR,
)


class WebSocketManager:
    """Tracks active WebSocket connections and broadcasts events to them."""

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------
    async def connect(self, websocket: WebSocket) -> None:
        """Accept the upgrade and register *websocket*."""

        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)
        logger.debug(
            "WebSocketManager: client connected (total=%d)",
            len(self._connections),
        )

    async def disconnect(self, websocket: WebSocket) -> None:
        """Remove *websocket* from the active set.

        No-op if the connection was already removed (e.g. by a failed
        broadcast). Safe to call multiple times.
        """

        async with self._lock:
            self._connections.discard(websocket)
        logger.debug(
            "WebSocketManager: client disconnected (total=%d)",
            len(self._connections),
        )

    # ------------------------------------------------------------------
    # Broadcast
    # ------------------------------------------------------------------
    async def broadcast(self, event: Event) -> None:
        """Send the JSON-serialized *event* to every connected client.

        Connections that fail to send (closed sockets, network errors) are
        silently dropped from the active set.
        """

        # Snapshot the set so concurrent connect/disconnect don't mutate us
        # mid-loop.
        async with self._lock:
            targets = list(self._connections)

        if not targets:
            return

        payload = event.model_dump_json()
        results = await asyncio.gather(
            *(self._send(ws, payload) for ws in targets),
            return_exceptions=True,
        )

        dead: list[WebSocket] = []
        for ws, result in zip(targets, results):
            if isinstance(result, Exception):
                dead.append(ws)
                logger.debug(
                    "WebSocketManager: dropping client after send error: %r",
                    result,
                )

        if dead:
            async with self._lock:
                for ws in dead:
                    self._connections.discard(ws)

    @staticmethod
    async def _send(websocket: WebSocket, payload: str) -> None:
        await websocket.send_text(payload)

    # ------------------------------------------------------------------
    # Bus integration
    # ------------------------------------------------------------------
    def subscribe_to_bus(self, bus: EventBus) -> None:
        """Wire the manager's :meth:`broadcast` into *bus*.

        Subscribes to every type in :data:`BROADCAST_TYPES`.
        """

        for event_type in BROADCAST_TYPES:
            bus.subscribe(event_type, self.broadcast)

    @property
    def active_count(self) -> int:
        """Number of connections currently in the active set."""

        return len(self._connections)
