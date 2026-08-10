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
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from anthropic import AsyncAnthropic
from fastapi import FastAPI

from config import CategoryConfigError, Settings, load_sources_config
from core.api import (
    PREF_DISTILL_THRESHOLD,
    PREF_MAX_DISPLAYED,
    PREF_SCORE_CUTOFF,
    create_app,
)
from core.events import EventBus
from core.state import StateStore
from core.websocket import WebSocketManager
from schemas.events import Event, EventType
from services.pipeline import Pipeline
from services.poller import Poller
from services.profile import ProfileDistiller
from services.scorer import ClaudeScorer
from services.sources.registry import SourceRegistry
from services.sources.substack import SubstackDriver
from services.summarizer import ClaudeSummarizer

logger = logging.getLogger(__name__)


PROFILE_VERSION = "profile-v1"
"""The version stamped on the profile seeded from ``PROFILE_PATH``.

A constant, not a hash of the file: seeding is insert-if-absent, so the file is
a starting point and the table is authoritative once anything has written to
it. Editing ``profile.md`` after the first boot therefore changes nothing —
phase 4 owns editing the live profile, and it appends a new version so the
scores stamped with the old one stay interpretable.
"""


async def _seed_profile_from_file(
    store: StateStore, path: str, failures: list[dict[str, Any]]
) -> None:
    """Seed the taste profile from *path*, if it exists and is readable.

    Every problem here is a logged failure entry rather than a raise, following
    ``sources.yaml``: an absent profile means scoring is off, not that the
    server is down. Articles still ingest and summarize, and an unscored article
    is a readable article with a blank score.

    Reports only what went wrong with the *file*. Whether scoring ends up
    enabled is decided once, below, where the API key is also known.
    """

    p = Path(path)
    if not p.exists():
        return

    try:
        body = p.read_text(encoding="utf-8")
    except OSError as exc:
        failures.append({
            "component": "profile",
            "error": f"could not read {path}: {type(exc).__name__}: {exc}",
        })
        logger.exception("main: could not read taste profile %s", path)
        return

    if not body.strip():
        failures.append({
            "component": "profile",
            "error": f"{path} is empty; a blank profile would score every "
                     f"article against nothing",
        })
        logger.error("main: taste profile %s is empty", path)
        return

    await store.seed_profile(PROFILE_VERSION, body)

    # Seeding is insert-if-absent by design — the file must never clobber a
    # version the reader approved, least of all a distilled one from phase 4.
    # The cost is that editing profile.md after first boot does nothing, and
    # doing nothing silently is the part that misleads: the operator changes
    # their taste, restarts, sees identical scores, and has no reason to suspect
    # the file was ignored rather than the rubric being unmoved by the edit.
    # Compared against PROFILE_VERSION specifically, never against "whichever
    # profile is currently in effect". Those diverge the moment phase 4 approves
    # a distilled version: the newest-approved row would then differ from an
    # untouched file, firing this warning on every boot forever and — far worse
    # — advising deletion of the distilled profile, which is the only stored
    # copy of the text every score stamped with it was judged against. This row
    # is the only one seeding could have written, and the only one the file has
    # any claim on.
    #
    # And it is only worth comparing while that row is still the profile in
    # effect. Once a later version has been approved — a distillation, or a
    # Settings edit — the file has done its one job and any difference is
    # expected rather than a problem: reporting it would put a permanent entry
    # in /health from the reader's first edit onward, and the remedy below
    # would tell them to delete the seeded row, which is not what is in effect
    # and whose deletion would leave the newer text untouched but the warning
    # still firing. "In effect" rather than "is the only row" on purpose: a
    # proposal that is pending or was rejected leaves the seed live, the file's
    # edits still inert, and the remedy still correct, so the warning must
    # survive both.
    latest = await store.latest_profile()
    if latest is not None and latest[0] != PROFILE_VERSION:
        return

    seeded = await store.profile_body(PROFILE_VERSION)
    if seeded is not None and seeded != body:
        failures.append({
            "component": "profile",
            "error": (
                f"{path} differs from {PROFILE_VERSION}, which was seeded from "
                f"it and is what scoring uses. Seeding never overwrites, so the "
                f"file's edits have no effect. To adopt them, delete the seeded "
                f"row and restart: "
                f"DELETE FROM profile_versions WHERE version = '{PROFILE_VERSION}';"
            ),
        })
        logger.warning(
            "main: %s differs from seeded profile %s — the stored text is in "
            "effect; scores will not reflect the file's edits",
            path, PROFILE_VERSION,
        )


async def _build_components(
    settings: Settings,
    http: httpx.AsyncClient,
) -> tuple[
    EventBus,
    WebSocketManager,
    StateStore,
    Poller,
    ProfileDistiller | None,
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
    await store.seed_preference(
        PREF_SCORE_CUTOFF, str(settings.DEFAULT_SCORE_CUTOFF)
    )
    await store.seed_preference(
        PREF_MAX_DISPLAYED, str(settings.DEFAULT_MAX_DISPLAYED)
    )
    await store.seed_preference(
        PREF_DISTILL_THRESHOLD, str(settings.DEFAULT_DISTILL_THRESHOLD)
    )

    # The profile is data seeded from a file, exactly like sources.yaml — and
    # like it, a problem with the file degrades a feature rather than the boot.
    await _seed_profile_from_file(store, settings.PROFILE_PATH, failures)
    profile = await store.latest_profile()

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
    except CategoryConfigError as exc:
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
        # Only the categories block was rejected, so the rest of the file is
        # still good: keep the real sources — a one-word typo in `filters`
        # must not silently halt all ingestion — and hard-zero both category
        # lists, so the summarizer tags nothing and the UI shows no filter row.
        sources = exc.config.sources
        vocabulary = []
        filters = []
    except Exception as exc:
        failures.append({
            "component": "sources_config",
            "error": f"could not load {settings.SOURCES_CONFIG_PATH}: "
                     f"{type(exc).__name__}: {exc}",
        })
        logger.exception(
            "main: could not load sources config %s — booting with no sources",
            settings.SOURCES_CONFIG_PATH,
        )
        # The file as a whole is unusable, so nothing from it is trustworthy.
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
        client = None
    else:
        # One client for both stages: they talk to the same API with the same
        # credentials, and sharing it shares the connection pool.
        client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        summarizer = ClaudeSummarizer(
            client,
            model=settings.SUMMARY_MODEL,
            max_input_chars=settings.SUMMARY_MAX_INPUT_CHARS,
            vocabulary=vocabulary,
        )

    # The distiller needs only the key. The scorer below also needs a profile to
    # judge against, but proposing a profile is what this one is for — gating it
    # on having one would make the empty case, the case that most needs a
    # proposal, the one that cannot ask for it. Without a key it stays None and
    # `POST /profile/review` answers 503.
    #
    # `SCORE_MODEL` rather than `SUMMARY_MODEL`: distillation is the same kind
    # of work scoring is — a judgment call over the whole corpus — not the
    # grounded extraction a small model handles well.
    distiller = (
        None
        if client is None
        else ProfileDistiller(client, model=settings.SCORE_MODEL)
    )

    # Scoring needs both halves of the scoring function: the key to call the
    # model, and a profile to judge against. Missing either disables it, and
    # the entry says which — "scoring is off" without a reason is the kind of
    # thing an operator rediscovers a week later from an empty score column.
    if client is None or profile is None:
        missing = []
        if client is None:
            missing.append("ANTHROPIC_API_KEY is not set")
        if profile is None:
            # Deliberately not "is absent". The profile is None for an absent
            # file, an empty one, or an unreadable one, and the `profile` entry
            # above already says which. Asserting "absent" here contradicts it
            # for two of those three, and two /health rows disagreeing is worse
            # than one saying less.
            missing.append(f"no taste profile loaded from {settings.PROFILE_PATH}")
        # What still runs depends on which half is missing. Without a key there
        # is no summarizer either, so nothing is ingested at all — promising
        # ingestion here would contradict the `summarizer` entry sitting beside
        # it.
        consequence = (
            "Articles are still ingested and summarized; they stay unscored."
            if client is not None
            else "The poller is disabled too, so nothing is ingested."
        )
        failures.append({
            "component": "scorer",
            "error": f"scoring is disabled: {'; '.join(missing)}. {consequence}",
        })
        logger.error("main: scoring disabled — %s", "; ".join(missing))
        scorer = None
    else:
        # The profile read above gates scoring; it is deliberately not handed
        # to the scorer. The reader can approve a distilled profile at any time
        # and the live one changes underneath this process, so a scorer built
        # around the boot profile would keep judging against it — and keep
        # stamping its version — until a restart. The pipeline reads the live
        # profile once per run instead, and this branch only decides whether
        # there is a scoring path at all.
        scorer = ClaudeScorer(client, model=settings.SCORE_MODEL)

    # WebSocket manager — subscribes itself to the bus, so anything published
    # from here on reaches every connected browser.
    ws_manager = WebSocketManager()
    ws_manager.subscribe_to_bus(bus)

    pipeline = Pipeline(
        store=store, registry=registry, summarizer=summarizer, bus=bus,
        scorer=scorer,
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

    return bus, ws_manager, store, poller, distiller, vocabulary, filters, failures


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
                distiller,
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
            # Unlike the scorer, the distiller is wired whenever the key
            # exists: it needs no profile, since proposing one is the point.
            app.state.distiller = distiller
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
