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

Pantheon module contract: API routes live under ``/api`` (``/api/feed/``,
``/api/saved/``, ..., ``/api/ws``); ``/health`` stays at the root; the static
UI is served at ``/`` by :func:`mount_ui`, which must be the last thing
registered. The mount is optional — if ``settings.WEB_DIR`` does not exist
the app still boots and a warning is logged.
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
from schemas.preferences import PreferenceIn
from schemas.profile import ApprovalIn, ProfileIn
from schemas.source import SourceConfig, SourceRef
# The one import from `services` in this layer, and deliberate: two constants
# with no behavior, no cycle (`services.profile` imports nothing from `core`),
# and the alternative is restating the distillation prompt's version here where
# it would drift. Not a precedent — the API talks to services through
# `app.state`, and a second import wanting to appear here is a sign the wiring
# belongs in `main.py` instead.
from services.profile import DISTILL_VERSION, DistillationError, ProfileDistiller

if TYPE_CHECKING:  # pragma: no cover
    from config import Settings
    from core.events import EventBus
    from core.state import StateStore
    from core.websocket import WebSocketManager

logger = logging.getLogger(__name__)


UI_MOUNT_PATH = "/"
"""Where static frontend assets are mounted.

The browser opens ``http://<host>:8002/``. The mount matches every path, so
:func:`mount_ui` must run after every route is registered.
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


PREF_SCORE_CUTOFF = "score_cutoff"
PREF_MAX_DISPLAYED = "max_displayed"
PREF_DISTILL_THRESHOLD = "distill_threshold"
"""The runtime knob keys.

Constants rather than string literals because three places spell them — this
API, the seeder in ``main.py``, and the tests — and a typo in any one of them
is a knob that silently reverts to its default on every read.
"""


class _IntKnob:
    """An integer preference: what it defaults to and what it accepts."""

    def __init__(self, default: int, low: int, high: int) -> None:
        self.default = default
        self.low = low
        self.high = high

    def contains(self, value: int) -> bool:
        return self.low <= value <= self.high


_PREFERENCES: dict[str, _IntKnob] = {
    # Mirrors ``Settings.DEFAULT_SCORE_CUTOFF`` / ``DEFAULT_MAX_DISPLAYED`` /
    # ``DEFAULT_DISTILL_THRESHOLD``, which are seeds for the ``preferences``
    # table. These are the fallbacks for a read that finds no row at all — an
    # app whose store was never seeded, or a key deleted by hand — so the two
    # must agree, or first-run behavior would change the moment the seeder ran.
    PREF_SCORE_CUTOFF: _IntKnob(default=0, low=0, high=100),
    PREF_MAX_DISPLAYED: _IntKnob(default=50, low=1, high=200),
    PREF_DISTILL_THRESHOLD: _IntKnob(default=20, low=5, high=200),
}
"""Every writable knob, with its range.

The ranges are not cosmetic. A cutoff outside 0-100 empties the feed, and a
``max_displayed`` below 1 reaches ``ranked_items`` as a negative slice bound,
which quietly displays *n-1* articles rather than failing. ``ranked_items``
trusts its ``limit``; this table is where that trust is earned. The distillation
threshold's floor is the same kind of guard on a different cost: below a handful
of ratings a proposal is a rewrite from noise, and every generation is a
long model call the reader pays for.
"""


async def _knob(store: "StateStore", key: str) -> int:
    """Read one knob, tolerating a stored value the API would have rejected.

    Preferences are TEXT, and nothing stops a hand-edited row from holding
    ``"seventy"`` or ``-1``. Both fall back to the default with a warning rather
    than raising: an unreadable knob must not 500 the feed, and honoring an
    out-of-range one would mean ``GET /preferences/`` reporting a number that is
    not the one in effect.
    """

    knob = _PREFERENCES[key]
    raw = await store.get_preference(key)
    if raw is None:
        return knob.default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "api: preference %s holds a non-numeric value %r; using %d",
            key, raw, knob.default,
        )
        return knob.default
    if not knob.contains(value):
        logger.warning(
            "api: preference %s holds %d, outside %d-%d; using %d",
            key, value, knob.low, knob.high, knob.default,
        )
        return knob.default
    return value


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


def _build_ranked_router() -> APIRouter:
    router = APIRouter(prefix="/ranked", tags=["feed"])

    @router.get("/")
    async def list_ranked(
        request: Request,
        category: list[str] | None = Query(default=None),
    ) -> dict[str, Any]:
        """The gated feed: what is shown, plus what was withheld and why.

        Both knobs are read from ``preferences`` rather than taken as query
        parameters — the cutoff is a property of the reader, not of the request,
        so a bookmarked URL cannot pin a stale gate.

        ``displayed`` carries the same :class:`~schemas.article.FeedItem` as
        ``/feed/`` and ``/saved/``, assembled by the same ``_feed_item``. The
        three withheld groups are counts and score ranges only: a collapsed row
        needs to say how many and between what, and rendering it never needs the
        articles themselves.

        ``?category=`` composes exactly as it does on the feed, narrowing every
        group rather than only the displayed list.
        """

        store = _store(request)
        categories = _selected_categories(request, category)
        cutoff = await _knob(store, PREF_SCORE_CUTOFF)
        max_displayed = await _knob(store, PREF_MAX_DISPLAYED)

        result = await store.ranked_items(
            cutoff=cutoff, limit=max_displayed, categories=categories
        )
        return {
            "cutoff": cutoff,
            "max_displayed": max_displayed,
            "total": result["total"],
            "displayed": [_feed_item(row) for row in result["displayed"]],
            "above_cutoff": result["above_cutoff"],
            "below_cutoff": result["below_cutoff"],
            "unscored": result["unscored"],
        }

    return router


def _build_preferences_router() -> APIRouter:
    router = APIRouter(prefix="/preferences", tags=["preferences"])

    @router.get("/")
    async def list_preferences(request: Request) -> dict[str, int]:
        """Every knob and its effective value.

        One object rather than a per-key GET: the UI draws both controls
        together, and two round-trips could render a cutoff and a page size read
        from either side of a write.
        """

        store = _store(request)
        return {key: await _knob(store, key) for key in _PREFERENCES}

    @router.put("/{key}", status_code=status.HTTP_204_NO_CONTENT)
    async def set_preference(key: str, body: PreferenceIn, request: Request) -> None:
        """Write one knob.

        ``PUT`` because the client states a desired end state, and both
        rejections are 400s naming what would have been accepted — an unknown
        key or an out-of-range value is a caller bug, and the caller can only
        fix it if the response says what the bounds were.
        """

        knob = _PREFERENCES.get(key)
        if knob is None:
            valid = ", ".join(_PREFERENCES)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"unknown preference {key!r}; valid keys: {valid}",
            )
        if not knob.contains(body.value):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"invalid {key} {body.value}; valid range: "
                       f"{knob.low}-{knob.high}",
            )
        await _store(request).set_preference(key, str(body.value))

    return router


DISTILL_CORPUS_LIMIT = 200
"""How many rated articles at most are sent to the distiller.

``rated_articles()`` is uncapped by design and ordered newest-rating-first, so
truncating with ``[:N]`` here keeps the freshest signal and drops only the
oldest — the ratings least likely to describe what the reader wants now. Order
is what makes the truncation safe; a differently-ordered corpus could not be
sliced at all.

200 cards is roughly 30k tokens of prompt, which is many months of reading at
the default 20-rating cadence and still a fraction of the context. Uncapped, a
few hundred rated articles would bloat every proposal call for evidence the
model has already seen the shape of.
"""


def _distiller(request: Request) -> "ProfileDistiller | None":
    return request.app.state.distiller  # type: ignore[no-any-return]


def _build_profile_router() -> APIRouter:
    router = APIRouter(prefix="/profile", tags=["profile"])

    @router.get("/")
    async def get_profile(request: Request) -> dict[str, Any]:
        """The live profile, or an empty shape when there is none.

        Not a 404. With the profile editable from Settings, "no profile yet" is
        where a reader starts, and an empty textarea is where they write their
        first one — without ever creating ``profile.md``. A 404 would make the
        UI render an error page over the control that fixes it.
        """

        store = _store(request)
        current = await store.latest_profile()
        if current is None:
            return {"version": None, "body": "", "kind": None}
        version, body = current
        return {
            "version": version,
            "body": body,
            "kind": await store.profile_kind(version),
        }

    @router.put("/")
    async def put_profile(payload: ProfileIn, request: Request) -> dict[str, str]:
        """Replace the profile, appending a new ``stated`` version.

        Approved on write: a profile the reader typed has nobody but its author
        to approve it, and leaving it pending would park it behind the review
        panel where it would look like a distillation they never asked for.

        Blank is a 400 for the reason the distiller rejects a blank proposal —
        the scorer would go on judging every article, against nothing.
        """

        if not payload.body.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="the profile body cannot be empty",
            )
        version = await _store(request).create_profile_version(
            payload.body, "stated", True
        )
        return {"version": version}

    @router.get("/review")
    async def get_review(request: Request) -> dict[str, Any]:
        """Where the review loop stands: one state, not a set of flags.

        ``state`` is an enum — ``insufficient`` | ``ready`` | ``pending`` —
        because the three panels are mutually exclusive. A boolean ``ready``
        beside a nullable ``proposal`` would let the UI derive a fourth
        combination that has no rendering.

        ``count`` is reported even when it is not what gates the next action, so
        the panel can always say how far along the cadence is.
        """

        store = _store(request)
        count = await store.ratings_since_last_review()
        threshold = await _knob(store, PREF_DISTILL_THRESHOLD)
        proposal = await store.pending_proposal()

        if proposal is not None:
            state = "pending"
        elif count >= threshold:
            state = "ready"
        else:
            state = "insufficient"

        return {
            "state": state,
            "count": count,
            "threshold": threshold,
            "proposal": proposal,
        }

    @router.post("/review")
    async def generate_review(request: Request) -> dict[str, str]:
        """Distill a fresh proposal from the rated corpus.

        The pending check comes before the threshold check because an
        outstanding proposal resets the rating count: checking the threshold
        first would answer "not enough ratings" for a panel that is in fact
        showing a proposal awaiting review.

        Nothing here goes live. The row is written unapproved, which is what
        keeps :meth:`~core.state.StateStore.latest_profile` from serving it.
        """

        store = _store(request)
        distiller = _distiller(request)
        if distiller is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="the distiller is not configured; ANTHROPIC_API_KEY is not set",
            )

        if await store.pending_proposal() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="a proposal is already awaiting review",
            )

        count = await store.ratings_since_last_review()
        threshold = await _knob(store, PREF_DISTILL_THRESHOLD)
        if count < threshold:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"only {count} rating(s) since the last review; "
                       f"{threshold} are needed",
            )

        current = await store.latest_profile()
        rated = (await store.rated_articles())[:DISTILL_CORPUS_LIMIT]
        try:
            body = await distiller.propose(current[1] if current else "", rated)
        except DistillationError as exc:
            # 502, not 500: the request was valid and the server did its part —
            # the model call is an upstream dependency that failed, or returned
            # something unusable. The detail is the message the panel shows, so
            # the reader can tell "try again" from "something is wrong with the
            # data" without reading a log.
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"could not distill a profile: {exc}",
            ) from exc

        # Checked again, because the guard above is a check-then-write whose
        # window is the entire model call — seconds wide, and every `await` in
        # it yields the loop. Two tabs, or one double-click, both pass the first
        # check and both write, leaving two pending rows. `pending_proposal()`
        # returns only the newest, so resolving the visible one leaves the older
        # still pending: the panel reappears with a stale proposal that the
        # rating count can never grow past, and the reader paid for two calls.
        #
        # This narrows a seconds-wide hole to the microseconds between here and
        # the insert; it does not close it. Discarding a finished proposal is
        # the price of losing the race, not of the fix. Making it structural
        # would take a UNIQUE partial index over pending rows
        # (`WHERE approved_at IS NULL AND rejected_at IS NULL`), which is a
        # migration and a caught IntegrityError — worth it only if this ever has
        # to be airtight. Approval needs none of this: `resolve_proposal` is a
        # single atomic UPDATE ... WHERE ... RETURNING, so the loser of a
        # double-approve gets False and a 409 by construction.
        if await store.pending_proposal() is not None:
            logger.warning(
                "api: discarding a distilled proposal; another request wrote "
                "one while this one was waiting on the model"
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="a proposal is already awaiting review",
            )

        version = await store.create_profile_version(
            body, "distilled", False, distill_version=DISTILL_VERSION
        )
        return {"version": version}

    @router.post("/review/{version}/approve")
    async def approve(
        version: str, payload: ApprovalIn, request: Request
    ) -> dict[str, Any]:
        """Make a proposal live, optionally amended, optionally re-scoring.

        A supplied-but-blank body is a 400 rather than an approval:
        ``resolve_proposal`` treats ``""`` as a real edit and would write it, so
        the reader would end up with an approved, live, empty profile that the
        scorer judges every article against. The state layer deliberately does
        not guard this — it only distinguishes "no edit" from "an edit" — so the
        guard belongs here, next to the same rejection on ``PUT /profile/``.

        Approval takes effect on the **next poll of each source**, not at the
        moment this returns: :meth:`services.pipeline.Pipeline.process_source`
        reads the live profile once per run, so scores already on screen do not
        move until their source is polled again. ``rescore`` only clears the
        checkpoints that make those articles eligible; it schedules nothing.
        """

        if payload.body is not None and not payload.body.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="the profile body cannot be empty",
            )

        store = _store(request)
        if not await store.resolve_proposal(version, True, payload.body):
            raise _unresolvable(version)

        # How many articles were re-queued, which is how many carried a score
        # under the outgoing profile. Not the pending-score queue depth: that
        # would also count articles being scored for the first time, and
        # "rescored" would overstate what the new profile actually revisits.
        rescored = await store.clear_scores_for_rescore() if payload.rescore else 0
        return {"version": version, "rescored": rescored}

    @router.post("/review/{version}/reject", status_code=status.HTTP_204_NO_CONTENT)
    async def reject(version: str, request: Request) -> None:
        """Discard a proposal. The live profile is untouched.

        A rejection still counts as a review: the proposal row keeps its
        timestamp, so the rating counter restarts from it and the reader is not
        offered the same evidence again immediately.
        """

        if not await _store(request).resolve_proposal(version, False):
            raise _unresolvable(version)

    return router


def _unresolvable(version: str) -> HTTPException:
    """409, not 404: the panel is stale and the UI should refetch.

    ``resolve_proposal`` matches pending rows only, so a ``False`` covers both
    "no such version" and "already approved or rejected". The second is what
    actually happens — two tabs open on the same proposal, or a double-click —
    and it is a conflict with the current state rather than a missing resource.
    """

    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"profile version {version!r} is not awaiting review",
    )


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


class _UIFiles(StaticFiles):
    """The built UI at "/".

    Vite's hashed files under ``assets/`` never change, so they cache forever;
    everything else (``index.html``, the manifest, icons) is revalidated on
    every load so a rebuild is picked up. It also receives WebSocket connects
    that match no route (e.g. an old cached page retrying /ws); close those
    cleanly instead of letting StaticFiles assert on a non-HTTP scope."""

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "websocket":
            await WebSocket(scope, receive, send).close()
            return
        await super().__call__(scope, receive, send)

    def file_response(self, full_path, stat_result, scope, status_code=200):
        response = super().file_response(full_path, stat_result, scope, status_code)
        relative = Path(full_path).resolve().relative_to(Path(self.directory).resolve())
        response.headers["Cache-Control"] = (
            "public, max-age=31536000, immutable"
            if relative.parts[0] == "assets"
            else "no-cache"
        )
        return response


def mount_ui(app: FastAPI, settings: "Settings") -> None:
    """Serve the static UI at ``/``.

    Call this last: a mount at ``/`` matches every path, so any route
    registered after it is unreachable.
    """

    web_dir = Path(settings.WEB_DIR)
    if not web_dir.is_dir():
        logger.warning(
            "Static UI directory %s does not exist; skipping mount. "
            "Run `make build` to build the frontend.", web_dir
        )
        return
    app.mount(
        UI_MOUNT_PATH, _UIFiles(directory=str(web_dir), html=True), name="ui"
    )
    logger.info("Static UI mounted at %s -> %s", UI_MOUNT_PATH, web_dir)


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _build_status_router() -> APIRouter:
    """GET /api/status — the optional Pantheon module-status contract."""

    router = APIRouter(tags=["status"])

    @router.get("/status")
    async def module_status(request: Request) -> dict[str, Any]:
        state = request.app.state
        settings = getattr(state, "settings", None)
        failures = [f for f in getattr(state, "startup_failures", [])
                    if f.get("severity", "error") == "error"]
        usage = getattr(state, "claude_usage", None)
        store = getattr(state, "state_store", None)
        rows = await store.all_source_rows() if store is not None else []
        failing = sum(1 for row in rows if row["error_count"])
        polled = [row["last_polled_at"] for row in rows if row["last_polled_at"]]
        calls, errors = usage.counts() if usage is not None else (0, 0)
        key_missing = settings is None or not settings.ANTHROPIC_API_KEY

        problems = []
        if key_missing:
            problems.append("no Anthropic API key set")
        if failures and not key_missing:
            # Without a key the startup failures are that same cause again.
            problems.append(f"{len(failures)} startup failure(s)")
        if failing:
            problems.append(f"{failing} source(s) failing")
        degraded = bool(problems or failures)
        return {
            "state": "degraded" if degraded else "ok",
            "summary": "; ".join(problems) or None,
            "stats": [
                {"label": "Claude requests (24h)", "value": calls, "kind": "count"},
                {"label": "Last Claude call", "value": _iso(usage.last_call) if usage else None, "kind": "time"},
                {"label": "Claude errors (24h)", "value": errors, "kind": "count", "warn": errors > 0},
                {"label": "Last feed poll", "value": max(polled) if polled else None, "kind": "time"},
                {"label": "Sources failing", "value": failing, "kind": "count", "warn": failing > 0},
            ],
        }

    return router


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
    distiller: "ProfileDistiller | None" = None,
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
        distiller: The :class:`~services.profile.ProfileDistiller` backing
            ``POST /profile/review``. Left ``None`` when no API key is
            configured, so that route returns 503 exactly as
            ``POST /sources/{id}/poll`` does without a poller. Unlike the
            scorer, it needs no profile — proposing one is the point.

    The returned app has the wired components on ``app.state``:
    ``event_bus``, ``ws_manager``, ``state_store``, ``poller``, ``settings``,
    ``category_vocabulary``, ``category_filters``, ``distiller``.

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
    app.state.distiller = distiller

    api = APIRouter(prefix="/api")
    for build in (
        _build_feed_router,
        _build_saved_router,
        _build_ranked_router,
        _build_preferences_router,
        _build_profile_router,
        _build_articles_router,
        _build_categories_router,
        _build_sources_router,
        _build_ws_router,
        _build_status_router,
    ):
        api.include_router(build())
    app.include_router(api)

    if mount_static and settings is not None:
        mount_ui(app, settings)

    return app
