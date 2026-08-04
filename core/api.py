"""FastAPI application factory.

Wires the HTTP and WebSocket endpoints onto a single :class:`fastapi.FastAPI`
instance. Construction is a factory rather than a module-level singleton so
``main.py`` can inject the wired components — and so tests can build a
hermetic app with mocks.

Dependency-injection pattern
----------------------------

Endpoints use ``fastapi.Request.app.state`` to retrieve the wired components.
Storing them on ``app.state`` keeps handler signatures clean (no ``Depends``
boilerplate) and matches FastAPI's idiomatic "per-app dependencies" approach.
Access them through the small ``_bus(request)``-style helpers below rather
than reaching into ``app.state`` inline.

URL layout
----------

API routes live at the root (``/ws``, and the domain routers added as the
feature set grows). Static frontend
assets are mounted under ``/ui/`` so they cannot collide with API paths. The
mount is optional — if ``settings.WEB_DIR`` does not exist the app still
boots and a warning is logged.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import (
    APIRouter,
    FastAPI,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.staticfiles import StaticFiles

if TYPE_CHECKING:  # pragma: no cover
    from config import Settings
    from core.events import EventBus
    from core.websocket import WebSocketManager

logger = logging.getLogger(__name__)


UI_MOUNT_PATH = "/ui"
"""Where static frontend assets are mounted.

The browser opens ``http://<host>:8000/ui/``. Mounted under a sub-path to
avoid any chance of shadowing API routes.
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bus(request: Request) -> "EventBus":
    return request.app.state.event_bus  # type: ignore[no-any-return]


def _ws_manager(app_or_ws: Any) -> "WebSocketManager":
    # Accepts either a Request or a WebSocket — both expose ``.app``.
    return app_or_ws.app.state.ws_manager  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------


def _build_ws_router() -> APIRouter:
    router = APIRouter(tags=["websocket"])

    @router.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        manager = _ws_manager(websocket)
        await manager.connect(websocket)
        try:
            # The endpoint never sends data on its own — the manager
            # broadcasts via the event-bus subscription. We just keep the
            # connection alive and wait for client messages (which we
            # currently ignore) or disconnect.
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception:  # pragma: no cover - defensive
            logger.exception("WebSocket loop raised; closing connection")
        finally:
            await manager.disconnect(websocket)

    return router


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------


def create_app(
    *,
    event_bus: "EventBus",
    ws_manager: "WebSocketManager",
    settings: "Settings | None" = None,
    mount_static: bool = True,
) -> FastAPI:
    """Build and wire a :class:`FastAPI` instance.

    Args:
        event_bus: The process event bus.
        ws_manager: WebSocket manager, already subscribed to the bus.
        settings: Application settings; used to locate ``WEB_DIR`` for the
            static mount. May be ``None`` in tests.
        mount_static: If ``False``, skip the static-file mount entirely.
            Tests pass ``False`` to keep the app hermetic.

    The returned app has the wired components on ``app.state``:
    ``event_bus``, ``ws_manager``, ``settings``.

    As new components arrive (a state store, a scheduler, service clients),
    add them as keyword-only args here and assign them onto ``app.state``
    alongside the existing ones.
    """

    app = FastAPI(
        title="Hermes",
        description="FastAPI core with an event bus, WebSocket push, and a no-build web UI.",
        version="0.1.0",
    )

    app.state.event_bus = event_bus
    app.state.ws_manager = ws_manager
    app.state.settings = settings

    app.include_router(_build_ws_router())

    if mount_static and settings is not None:
        web_dir = Path(settings.WEB_DIR)
        if web_dir.exists() and web_dir.is_dir():
            app.mount(
                UI_MOUNT_PATH,
                StaticFiles(directory=str(web_dir), html=True),
                name="ui",
            )
            logger.info(
                "Static UI mounted at %s -> %s", UI_MOUNT_PATH, web_dir
            )
        else:
            logger.warning(
                "Static UI directory %s does not exist; skipping mount.",
                web_dir,
            )

    return app
