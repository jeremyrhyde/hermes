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
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import (
    APIRouter,
    FastAPI,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.staticfiles import StaticFiles

from core.state import parse_iso
from schemas.article import FeedItem, InteractionIn, RatingIn
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


def _vocabulary(request: Request) -> list[str]:
    return request.app.state.category_vocabulary  # type: ignore[no-any-return]


def _category_filters(request: Request) -> list[str]:
    return request.app.state.category_filters  # type: ignore[no-any-return]


def _selected_categories(
    request: Request, category: list[str] | None
) -> list[str]:
    """Normalize and validate the repeated ``?category=`` query parameter.

    Values are lowercased and de-duplicated (first occurrence wins, so the
    order the user clicked in survives). The de-duplication is load-bearing,
    not cosmetic: the store's AND filter binds
    ``COUNT(DISTINCT category) = len(categories)``, so a repeated value —
    ``?category=AI&category=ai``, one double-clicked filter button — would ask
    for a count that ``IN ('ai', 'ai')`` can never reach and silently return
    nothing.

    Validation is against the *vocabulary*, not ``filters``: ``filters`` only
    controls which buttons the UI draws, while tagged data exists for the whole
    vocabulary. An unrecognized value is a 400 naming the valid ones rather
    than a silent drop, because a dropped filter renders an unexplained empty
    feed that looks exactly like a broken deploy.
    """

    if not category:
        return []

    vocabulary = _vocabulary(request)
    selected: list[str] = []
    for raw in category:
        name = raw.strip().lower()
        if not name:  # ``?category=`` — an empty widget, not a selection
            continue
        if name not in vocabulary:
            valid = ", ".join(vocabulary) or "(none configured)"
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"unknown category {name!r}; valid categories: {valid}",
            )
        if name not in selected:
            selected.append(name)
    return selected


_SCOPES = {"feed": False, "saved": True}
"""``?scope=`` values, mapped to ``saved_only``."""


def _saved_only(scope: str) -> bool:
    """Resolve ``?scope=`` to the store's ``saved_only`` flag.

    An unrecognized scope is a 400 for the same reason an unknown category is:
    silently falling back to ``feed`` would render counts that confidently
    describe the wrong population, with nothing on screen to say so.
    """

    if scope not in _SCOPES:
        valid = ", ".join(_SCOPES)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown scope {scope!r}; valid scopes: {valid}",
        )
    return _SCOPES[scope]


def _feed_item(row: dict[str, Any]) -> FeedItem:
    """Assemble the wire DTO from one ``feed_items`` row.

    One assembly for ``/feed/`` and ``/saved/`` on purpose: they read identical
    rows, and two copies would drift the next time :class:`FeedItem` grows a
    field. That is exactly how the WebSocket payload came to be missing
    ``categories``.
    """

    return FeedItem(
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
        categories=row["categories"],
        saved=row["saved"],
    )


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------


def _build_feed_router() -> APIRouter:
    router = APIRouter(prefix="/feed", tags=["feed"])

    @router.get("/", response_model=list[FeedItem])
    async def list_feed(
        request: Request,
        limit: int = 50,
        offset: int = 0,
        category: list[str] | None = Query(default=None),
    ) -> list[FeedItem]:
        """The rendered feed, newest first.

        The DTO is assembled here from plain query rows rather than serialized
        from a stored ``Article`` (spec section 12.5). That keeps the wire
        format — badges, read state, per-item actions — free to evolve without
        dragging the storage schema along with it, and lets source identity be
        denormalized into every card.

        Repeated ``?category=`` values narrow the feed to articles carrying
        **all** of them (AND semantics).
        """

        categories = _selected_categories(request, category)
        rows = await _store(request).feed_items(
            limit=limit, offset=offset, categories=categories
        )
        return [_feed_item(row) for row in rows]

    return router


def _build_saved_router() -> APIRouter:
    router = APIRouter(prefix="/saved", tags=["saved"])

    @router.get("/", response_model=list[FeedItem])
    async def list_saved(
        request: Request,
        limit: int = 50,
        offset: int = 0,
        category: list[str] | None = Query(default=None),
    ) -> list[FeedItem]:
        """Saved articles, newest-saved first.

        The same rows and the same DTO as ``/feed/``, narrowed to pinned
        articles and reordered by when they were pinned. ``?category=`` composes
        exactly as it does on the feed, so the filter row can be reused verbatim
        against this list.
        """

        categories = _selected_categories(request, category)
        rows = await _store(request).feed_items(
            limit=limit, offset=offset, categories=categories, saved_only=True
        )
        return [_feed_item(row) for row in rows]

    @router.post("/{article_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def save(article_id: int, request: Request) -> None:
        """Pin an article. Idempotent; 404 only for an unknown article."""

        if not await _store(request).save_article(
            article_id, datetime.now(timezone.utc)
        ):
            raise _unknown_article(article_id)

    @router.delete("/{article_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def unsave(article_id: int, request: Request) -> None:
        """Unpin an article.

        404 is about the article, not the saved state: unpinning something that
        was never pinned is a successful no-op. The store's boolean is existence
        for exactly this reason — do not re-derive it by reading saved state.
        """

        if not await _store(request).unsave_article(article_id):
            raise _unknown_article(article_id)

    return router


def _unknown_article(article_id: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"article {article_id} not found",
    )


def _build_categories_router() -> APIRouter:
    router = APIRouter(prefix="/categories", tags=["categories"])

    @router.get("/")
    async def list_categories(
        request: Request,
        category: list[str] | None = Query(default=None),
        scope: str = "feed",
    ) -> dict[str, Any]:
        """The filter row: one entry per configured filter, with its count.

        Only the configured ``filters`` are returned (spec section 6.1) — the
        wider vocabulary is what Claude may assign, not what the UI offers, so
        exposing it here would invite buttons nobody asked for. An unconfigured
        app returns an empty row rather than 404.

        Counts are contextual: each is how many articles would remain if that
        filter were *also* selected, so a dead-end combination can render
        disabled instead of being discovered by clicking it.

        ``?scope=saved`` counts over saved articles only, so the Saved tab can
        reuse this endpoint — and therefore the one contextual-counting
        implementation and the one response contract — rather than growing a
        parallel ``/saved/categories/``.
        """

        saved_only = _saved_only(scope)
        selected = _selected_categories(request, category)
        filters = _category_filters(request)
        if not filters:
            return {"selected": selected, "filters": []}

        counts = await _store(request).category_counts(
            filters, selected=selected, saved_only=saved_only
        )
        return {
            "selected": selected,
            "filters": [
                {
                    "category": name,
                    "count": counts[name],
                    "selected": name in selected,
                }
                for name in filters
            ],
        }

    return router


_INTERACTION_KINDS = ("expand", "click_through")
"""Mirrors the CHECK constraint on ``interactions.kind``.

Validated here so an unknown kind is a 400 naming the valid ones, rather than a
constraint violation surfacing as a 500.
"""


def _build_articles_router() -> APIRouter:
    router = APIRouter(prefix="/articles", tags=["feedback"])

    @router.put("/{article_id}/rating", status_code=status.HTTP_204_NO_CONTENT)
    async def rate(article_id: int, body: RatingIn, request: Request) -> None:
        """Rate an article ±1.

        ``PUT`` because the client states a desired end state — "my rating is
        +1" — which is idempotent from the user's side even though the
        append-only log grows underneath.
        """

        if body.value not in (-1, 1):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"invalid rating {body.value}; valid values: -1, 1",
            )
        if not await _store(request).rate_article(
            article_id, body.value, datetime.now(timezone.utc)
        ):
            raise _unknown_article(article_id)

    @router.delete("/{article_id}/rating", status_code=status.HTTP_204_NO_CONTENT)
    async def unrate(article_id: int, request: Request) -> None:
        """Clear an article's rating.

        ``CHECK (value IN (-1, 1))`` makes neutral unrepresentable, so undoing a
        misclick means deleting. Without this a misclick would be permanent, and
        a permanent misclick is exactly the bad signal that teaches the taste
        profile the wrong thing. Clearing an unrated article is a 204 no-op;
        only an unknown article is a 404.
        """

        if not await _store(request).clear_rating(article_id):
            raise _unknown_article(article_id)

    @router.post("/{article_id}/interactions", status_code=status.HTTP_204_NO_CONTENT)
    async def interact(
        article_id: int, body: InteractionIn, request: Request
    ) -> None:
        """Log an interaction. Never deduplicated — repetition is signal."""

        if body.kind not in _INTERACTION_KINDS:
            valid = ", ".join(_INTERACTION_KINDS)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"unknown interaction kind {body.kind!r}; valid kinds: {valid}",
            )
        if not await _store(request).record_interaction(
            article_id, body.kind, datetime.now(timezone.utc)
        ):
            raise _unknown_article(article_id)

    return router


def _build_sources_router() -> APIRouter:
    router = APIRouter(prefix="/sources", tags=["sources"])

    @router.get("/")
    async def list_sources(request: Request) -> list[dict[str, Any]]:
        """Source health. Disabled sources stay visible so they cannot rot."""

        store = _store(request)
        rows = await store.all_source_rows()
        # unusable_counts() cannot emit a row for a source with none, so the
        # zero is filled here. It must be present rather than absent: a missing
        # key renders as `undefined` in the template, which is exactly how the
        # WebSocket payload broke once by omitting `categories`.
        unusable = await store.unusable_counts()
        return [
            {
                "unusable_count": unusable.get(row["id"], 0),
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
    category_vocabulary: list[str] | None = None,
    category_filters: list[str] | None = None,
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
        category_vocabulary: Every category Claude may assign — what the
            ``?category=`` query parameter validates against.
        category_filters: The subset the UI offers as buttons, in display
            order. Empty means categories are unconfigured, and
            ``GET /categories/`` returns an empty filter row.
        mount_static: If ``False``, skip the static-file mount entirely.
            Tests pass ``False`` to keep the app hermetic.

    The returned app has the wired components on ``app.state``:
    ``event_bus``, ``ws_manager``, ``state_store``, ``poller``, ``settings``,
    ``category_vocabulary``, ``category_filters``.

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
    app.state.category_vocabulary = category_vocabulary or []
    app.state.category_filters = category_filters or []

    app.include_router(_build_feed_router())
    app.include_router(_build_saved_router())
    app.include_router(_build_articles_router())
    app.include_router(_build_categories_router())
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
