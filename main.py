"""Application entry point.

Wires every layer of Hermes together and starts the FastAPI server.

Initialization order:

1. :class:`config.Settings`
2. :class:`core.events.EventBus`
3. Domain services from ``services/`` (none yet — see ``_build_components``)
4. :class:`core.websocket.WebSocketManager` (subscribed to the bus)
5. :func:`core.api.create_app` -> :class:`fastapi.FastAPI`

Shutdown is the reverse of startup: stop anything with a lifecycle, in the
opposite order it was started.

Usage::

    uv run python main.py
    # or
    uv run uvicorn main:app --host 0.0.0.0 --port 8000

The lifespan context handles startup/shutdown for both invocations, so
module import performs no I/O.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI

from config import Settings
from core.api import create_app
from core.events import EventBus
from core.websocket import WebSocketManager
from schemas.events import Event, EventType

logger = logging.getLogger(__name__)


async def _build_components(
    settings: Settings,
) -> tuple[EventBus, WebSocketManager, list[dict[str, Any]]]:
    """Build and wire every runtime component.

    Returns the components plus a list of startup failures for ``/health``.
    A failure dict has the shape ``{"component": str, "error": str}``.

    A component that fails to construct should be appended to ``failures``
    and logged rather than raised — the server stays up and an operator can
    see exactly what did not come online at ``/health``.
    """

    failures: list[dict[str, Any]] = []

    # 1. Event bus.
    bus = EventBus()

    # 2. Domain services go here. Construct each one, hand it ``bus``, and
    #    ``await service.start()`` if it has a lifecycle. Wrap each in
    #    try/except and append to ``failures`` on error.

    # 3. WebSocket manager — subscribes itself to the bus, so anything
    #    published from here on reaches every connected browser.
    ws_manager = WebSocketManager()
    ws_manager.subscribe_to_bus(bus)

    return bus, ws_manager, failures


def _make_lifespan(settings: Settings):
    """Build a lifespan context bound to *settings*.

    The lifespan owns the actual component lifecycle so unit tests can
    construct an app without spinning up the real one.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        bus, ws_manager, failures = await _build_components(settings)

        # Wire components onto app.state so endpoints + /health can read them.
        app.state.event_bus = bus
        app.state.ws_manager = ws_manager
        app.state.settings = settings
        app.state.startup_failures = failures

        logger.info("main: ready — %d startup failure(s)", len(failures))
        await bus.publish(
            Event(
                type=EventType.SYSTEM_READY,
                data={"detail": f"listening on {settings.HOST}:{settings.PORT}"},
                source="startup",
            )
        )

        try:
            yield
        finally:
            logger.info("main: shutting down")
            # Stop services here in reverse construction order. Wrap each
            # call in try/except so one failing teardown doesn't skip the
            # rest.

    return lifespan


def _configure_logging(level: str) -> None:
    """Configure the root logger from ``settings.LOG_LEVEL``."""

    numeric = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


def build_app(settings: Settings | None = None) -> FastAPI:
    """Construct the public :class:`FastAPI` instance with lifespan wiring.

    The actual component initialisation happens inside the lifespan context
    so importing this module does not perform I/O.
    """

    settings = settings or Settings()
    _configure_logging(settings.LOG_LEVEL)

    # Placeholders for the FastAPI factory — the real values are written into
    # app.state inside the lifespan. They would only be touched by a request
    # arriving before lifespan startup finished, which FastAPI's lifespan
    # contract prevents.
    app = create_app(
        event_bus=EventBus(),
        ws_manager=WebSocketManager(),
        settings=settings,
    )
    app.router.lifespan_context = _make_lifespan(settings)

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, Any]:
        """Liveness + boot summary.

        Returns the startup failure list so operators can confirm what came
        up without scraping the logs.
        """

        return {
            "status": "ok",
            "startup_failures": getattr(app.state, "startup_failures", []),
        }

    return app


# ``uvicorn main:app`` resolves this top-level binding.
app = build_app()


if __name__ == "__main__":
    settings = Settings()
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        log_level=settings.LOG_LEVEL.lower(),
        reload=False,
    )
