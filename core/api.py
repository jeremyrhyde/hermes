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

API routes live at the root: ``/ws`` for the WebSocket, ``/feed/`` for the
rendered feed, ``/sources/`` for source health and manual polls, plus the
domain routers added as the feature set grows. Static frontend assets are
mounted under ``/ui/`` so they cannot collide with API paths. The mount is
optional — if ``settings.WEB_DIR`` does not exist the app still boots and a
warning is logged.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import (
    APIRouter,
    FastAPI,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.staticfiles import StaticFiles

from core.state import parse_iso
from schemas.article import FeedItem
from schemas.source import SourceConfig, SourceRef

if TYPE_CHECKING:  # pragma: no cover
    from config import Settings
    from core.events import EventBus
    from core.state import StateStore
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


def _store(request: Request) -> "StateStore":
    return request.app.state.state_store  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------


def _build_feed_router() -> APIRouter:
    router = APIRouter(prefix="/feed", tags=["feed"])

    @router.get("/", response_model=list[FeedItem])
    async def list_feed(
        request: Request, limit: int = 50, offset: int = 0
    ) -> list[FeedItem]:
        """The rendered feed, newest first.

        The DTO is assembled here from plain query rows rather than serialized
        from a stored ``Article`` (spec section 12.5). That keeps the wire
        format — badges, read state, per-item actions — free to evolve without
        dragging the storage schema along with it, and lets source identity be
        denormalized into every card.
        """

        rows = await _store(request).feed_items(limit=limit, offset=offset)
        return [
            FeedItem(
                article_id=row["article_id"],
                headline=row["headline"],
                bullets=json.loads(row["bullets_json"]),
                url=row["url"],
                published_at=parse_iso(row["published_at"]),
                source=SourceRef(
                    id=row["source_id"],
                    name=row["source_name"],
                    type=row["source_type"],
                ),
                score=row["score"],
                rating=row["rating"],
            )
            for row in rows
        ]

    return router


def _build_sources_router() -> APIRouter:
    router = APIRouter(prefix="/sources", tags=["sources"])

    @router.get("/")
    async def list_sources(request: Request) -> list[dict[str, Any]]:
        """Source health. Disabled sources stay visible so they cannot rot."""

        rows = await _store(request).all_source_rows()
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "type": row["type"],
                "enabled": bool(row["enabled"]),
                "error_count": row["error_count"],
                "disabled": row["disabled_until"] is not None,
                "disabled_until": row["disabled_until"],
                "last_polled_at": row["last_polled_at"],
                "next_poll_at": row["next_poll_at"],
            }
            for row in rows
        ]

    @router.post("/{source_id}/poll")
    async def poll_now(source_id: str, request: Request) -> dict[str, Any]:
        """Trigger an immediate poll. Used during phase 1 user testing."""

        poller = request.app.state.poller
        if poller is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="poller is not running",
            )

        row = await _store(request).get_source_row(source_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"source {source_id!r} not found",
            )

        await poller.poll_one(
            SourceConfig(
                id=row["id"], type=row["type"], name=row["name"],
                feed_url=row["feed_url"], enabled=bool(row["enabled"]),
            )
        )
        return {"polled": source_id}

    return router


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
    state_store: "StateStore | None" = None,
    poller: Any = None,
    settings: "Settings | None" = None,
    mount_static: bool = True,
) -> FastAPI:
    """Build and wire a :class:`FastAPI` instance.

    Args:
        event_bus: The process event bus.
        ws_manager: WebSocket manager, already subscribed to the bus.
        state_store: The started :class:`~core.state.StateStore` backing the
            feed and sources routes. ``None`` until the lifespan wires it.
        poller: The source poller backing ``POST /sources/{id}/poll``. Left
            ``None`` when no API key is configured, so that route returns 503
            rather than driving a pipeline with no summarizer.
        settings: Application settings; used to locate ``WEB_DIR`` for the
            static mount. May be ``None`` in tests.
        mount_static: If ``False``, skip the static-file mount entirely.
            Tests pass ``False`` to keep the app hermetic.

    The returned app has the wired components on ``app.state``:
    ``event_bus``, ``ws_manager``, ``state_store``, ``poller``, ``settings``.

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
    app.state.state_store = state_store
    app.state.poller = poller
    app.state.settings = settings

    app.include_router(_build_feed_router())
    app.include_router(_build_sources_router())
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
