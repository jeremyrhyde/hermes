"""Application entry point.

Wires every layer of Hermes together and starts the FastAPI server.

Initialization order:

1. :class:`config.Settings`
2. :class:`core.events.EventBus`
3. :class:`core.state.StateStore` (opened, migrated, sources upserted)
4. Domain services from ``services/``: the source registry and its drivers,
   the summarizer, the pipeline, and the poller
5. :class:`core.websocket.WebSocketManager` (subscribed to the bus)
6. :func:`core.api.create_app` -> :class:`fastapi.FastAPI`

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

import httpx
import uvicorn
from anthropic import AsyncAnthropic
from fastapi import FastAPI

from config import Settings, load_sources_config
from core.api import create_app
from core.events import EventBus
from core.state import StateStore
from core.websocket import WebSocketManager
from schemas.events import Event, EventType
from services.pipeline import Pipeline
from services.poller import Poller
from services.sources.registry import SourceRegistry
from services.sources.substack import SubstackDriver
from services.summarizer import ClaudeSummarizer

logger = logging.getLogger(__name__)


async def _build_components(
    settings: Settings,
    http: httpx.AsyncClient,
) -> tuple[
    EventBus,
    WebSocketManager,
    StateStore,
    Poller,
    list[str],
    list[str],
    list[dict[str, Any]],
]:
    """Build and wire every runtime component.

    Returns the components, the category vocabulary and filter list read from
    the sources config, plus a list of startup failures for ``/health``.
    A failure dict has the shape ``{"component": str, "error": str}``.

    A component that fails to construct is appended to ``failures`` and logged
    rather than raised — the server stays up and an operator sees exactly what
    did not come online at ``/health``.

    Args:
        settings: Application settings.
        http: The shared HTTP client handed to the source drivers. Owned by
            the caller (the lifespan), which closes it on shutdown — see
            :func:`_make_lifespan`.
    """

    failures: list[dict[str, Any]] = []

    bus = EventBus()

    store = StateStore(settings.DB_PATH)
    await store.start()
    await store.seed_preference("score_cutoff", str(settings.DEFAULT_SCORE_CUTOFF))
    await store.seed_preference("max_displayed", str(settings.DEFAULT_MAX_DISPLAYED))

    # Sources: declarative fields are refreshed from YAML on every boot; poll
    # state (etag/hash/backoff) is deliberately preserved by upsert_source.
    #
    # A malformed sources.yaml (bad YAML syntax, or a shape that fails
    # validation) must not kill the process: the operator gets a running
    # server telling them what is wrong at /health, not a dead one. The same
    # holds for a categories block whose filters are not all in the
    # vocabulary — load_sources_config raises for that from inside this call.
    try:
        config = load_sources_config(settings.SOURCES_CONFIG_PATH)
        sources = config.sources
        vocabulary = config.categories.vocabulary
        filters = config.categories.filters
    except Exception as exc:
        # The category check raises a bare ValueError; every other failure
        # mode here arrives as a YAMLError, an OSError, or pydantic's
        # ValidationError. The type check must be exact — ValidationError
        # subclasses ValueError, so isinstance would mislabel it.
        is_category_error = type(exc) is ValueError
        if is_category_error:
            failures.append({
                "component": "categories_config",
                "error": f"invalid categories block in "
                         f"{settings.SOURCES_CONFIG_PATH}: {exc}",
            })
            logger.exception(
                "main: invalid categories block in %s — booting with "
                "categories disabled",
                settings.SOURCES_CONFIG_PATH,
            )
        else:
            failures.append({
                "component": "sources_config",
                "error": f"could not load {settings.SOURCES_CONFIG_PATH}: "
                         f"{type(exc).__name__}: {exc}",
            })
            logger.exception(
                "main: could not load sources config %s — booting with no sources",
                settings.SOURCES_CONFIG_PATH,
            )
        # Whatever went wrong, nothing from the file is trustworthy: no
        # sources, and both category lists empty so the summarizer tags
        # nothing and the UI shows no filter row.
        sources = []
        vocabulary = []
        filters = []

    if not sources:
        logger.warning(
            "main: no sources configured (looked at %s). Server will boot with "
            "an empty feed — copy sources.yaml.example to sources.yaml.",
            settings.SOURCES_CONFIG_PATH,
        )
    for cfg in sources:
        await store.upsert_source(cfg)

    registry = SourceRegistry()
    registry.register(SubstackDriver(http))

    if not settings.ANTHROPIC_API_KEY:
        failures.append({
            "component": "summarizer",
            "error": "ANTHROPIC_API_KEY is not set; polling is disabled, so "
                     "nothing is ingested or summarized",
        })
        logger.error(
            "main: ANTHROPIC_API_KEY is not set — polling and summarization disabled"
        )
        summarizer = None
    else:
        summarizer = ClaudeSummarizer(
            AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY),
            model=settings.SUMMARY_MODEL,
            max_input_chars=settings.SUMMARY_MAX_INPUT_CHARS,
            vocabulary=vocabulary,
        )

    # WebSocket manager — subscribes itself to the bus, so anything published
    # from here on reaches every connected browser.
    ws_manager = WebSocketManager()
    ws_manager.subscribe_to_bus(bus)

    pipeline = Pipeline(
        store=store, registry=registry, summarizer=summarizer, bus=bus
    )
    poller = Poller(
        store=store,
        pipeline=pipeline,
        bus=bus,
        min_seconds=settings.POLL_MIN_SECONDS,
        max_seconds=settings.POLL_MAX_SECONDS,
        tick_seconds=settings.POLL_TICK_SECONDS,
    )
    # Without a summarizer the loop stays parked: the pipeline would ingest
    # articles it can never summarize, burning bandwidth for nothing.
    if summarizer is not None:
        await poller.start()

    return bus, ws_manager, store, poller, vocabulary, filters, failures


def _make_lifespan(settings: Settings):
    """Build a lifespan context bound to *settings*.

    The lifespan owns the actual component lifecycle so unit tests can
    construct an app without spinning up the real one.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # The HTTP client is owned here rather than by any one component:
        # ``async with`` binds its lifetime to the lifespan scope, so it is
        # closed even if _build_components raises part-way through startup.
        async with httpx.AsyncClient(timeout=30.0) as http:
            (
                bus,
                ws_manager,
                store,
                poller,
                vocabulary,
                filters,
                failures,
            ) = await _build_components(settings, http)

            # Wire components onto app.state so endpoints + /health can read
            # them.
            app.state.event_bus = bus
            app.state.ws_manager = ws_manager
            app.state.state_store = store
            # Only expose the poller if it actually started. Without an API key
            # the poller object exists but its loop was never launched, and the
            # manual POST /sources/{id}/poll route would drive a pipeline whose
            # summarizer is None. Exposing None makes that route return 503.
            app.state.poller = poller if poller.is_running else None
            app.state.settings = settings
            # Categories come from the config file, which is only read here —
            # create_app runs at import time, before any I/O has happened, so
            # it seeds both lists to [] and the lifespan fills them in.
            app.state.category_vocabulary = vocabulary
            app.state.category_filters = filters
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
                # Reverse construction order. Each teardown is wrapped so one
                # failure does not skip the rest.
                logger.info("main: shutting down")
                for label, coro in (
                    ("poller.stop", poller.stop()),
                    ("store.close", store.close()),
                ):
                    try:
                        await coro
                    except Exception:  # pragma: no cover
                        logger.exception("main: %s failed", label)

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
        state_store=None,
        poller=None,
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
