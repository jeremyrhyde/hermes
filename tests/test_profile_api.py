"""The profile and review routes."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from core.api import create_app
from core.events import EventBus
from core.state import StateStore
from core.websocket import WebSocketManager
from schemas.article import ArticleRef, Summary
from schemas.scoring import Score
from schemas.source import SourceConfig

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
CFG = SourceConfig(id="acx", type="substack", name="ACX", feed_url="https://x/feed")


class StubDistiller:
    def __init__(self, body: str = "# Distilled\n\nnew body") -> None:
        self.calls = 0
        self._body = body

    async def propose(self, current, rated):
        self.calls += 1
        return self._body


@pytest.fixture
def distiller() -> StubDistiller:
    return StubDistiller()


@pytest.fixture
def client(store: StateStore, distiller: StubDistiller) -> TestClient:
    bus = EventBus()
    ws = WebSocketManager()
    ws.subscribe_to_bus(bus)
    app = create_app(event_bus=bus, ws_manager=ws, state_store=store,
                     poller=None, settings=None, mount_static=False,
                     distiller=distiller)
    return TestClient(app)


async def _rate(store: StateStore, n: int) -> None:
    """*n* articles taken all the way to a rating, as the app would produce them.

    Scored before rated, and not incidentally: an article is only on the feed to
    be rated *because* it was scored and displayed. A summarized-but-unscored
    article is a state the pipeline cannot reach, and a fixture that builds one
    makes ``clear_scores_for_rescore`` — which counts cleared checkpoints —
    report zero for a corpus that plainly has one.
    """
    await store.upsert_source(CFG)
    for i in range(n):
        ref = ArticleRef(source_id="acx", guid=f"g{i}", url=f"https://x/{i}",
                         title=f"T{i}", published_at=NOW)
        article_id = await store.ingest_article(ref, ref.url, NOW)
        await store.save_extraction(article_id, "text", 500, "<p/>", NOW)
        await store.save_summary(
            article_id,
            Summary(headline=f"H{i}", bullets=["b"] * 5, model="m",
                    prompt_version="summary-v3", categories=["ai"]),
            summarized_at=NOW,
        )
        await store.save_score(
            article_id,
            Score(value=50 + i, rationale="r", rubric_version="rubric-v1",
                  profile_version="profile-v1"),
            NOW,
        )
        await store.rate_article(article_id, 1 if i % 2 else -1, NOW)


def test_profile_is_empty_when_unseeded(client: TestClient) -> None:
    """Not a 404: an empty textarea is where the first profile gets written."""
    body = client.get("/profile/").json()
    assert body["version"] is None
    assert body["body"] == ""


def test_editing_creates_a_new_version(client: TestClient) -> None:
    first = client.put("/profile/", json={"body": "one"}).json()["version"]
    second = client.put("/profile/", json={"body": "two"}).json()["version"]

    assert first != second
    assert client.get("/profile/").json()["body"] == "two"


def test_an_empty_profile_is_rejected(client: TestClient) -> None:
    """A blank profile would score every article against nothing."""
    assert client.put("/profile/", json={"body": "   "}).status_code == 400


async def test_review_reports_insufficient_below_threshold(
    store: StateStore, client: TestClient
) -> None:
    await _rate(store, 3)

    body = client.get("/profile/review").json()
    assert body["state"] == "insufficient"
    assert body["count"] == 3
    assert body["threshold"] == 20
    assert body["proposal"] is None


async def test_review_reports_ready_at_the_threshold(
    store: StateStore, client: TestClient
) -> None:
    await store.set_preference("distill_threshold", "5")
    await _rate(store, 5)

    assert client.get("/profile/review").json()["state"] == "ready"


async def test_generating_below_the_threshold_is_rejected(
    store: StateStore, client: TestClient, distiller: StubDistiller
) -> None:
    await _rate(store, 1)

    res = client.post("/profile/review")
    assert res.status_code == 400
    assert distiller.calls == 0, "no API call below the threshold"


async def test_generating_produces_a_pending_proposal(
    store: StateStore, client: TestClient, distiller: StubDistiller
) -> None:
    await store.set_preference("distill_threshold", "5")
    await store.create_profile_version("live", "stated", True)
    await _rate(store, 5)

    assert client.post("/profile/review").status_code == 200
    assert distiller.calls == 1

    body = client.get("/profile/review").json()
    assert body["state"] == "pending"
    assert body["proposal"]["body"] == "# Distilled\n\nnew body"


async def test_a_second_proposal_is_refused(
    store: StateStore, client: TestClient
) -> None:
    """Two diffs against the same base are jointly incoherent."""
    await store.set_preference("distill_threshold", "5")
    await _rate(store, 5)
    client.post("/profile/review")

    assert client.post("/profile/review").status_code == 409


async def test_a_pending_proposal_is_not_live(
    store: StateStore, client: TestClient
) -> None:
    await store.set_preference("distill_threshold", "5")
    await store.create_profile_version("live", "stated", True)
    await _rate(store, 5)
    client.post("/profile/review")

    assert client.get("/profile/").json()["body"] == "live"


async def test_approving_makes_it_live(store: StateStore, client: TestClient) -> None:
    await store.set_preference("distill_threshold", "5")
    await store.create_profile_version("live", "stated", True)
    await _rate(store, 5)
    version = client.post("/profile/review").json()["version"]

    res = client.post(f"/profile/review/{version}/approve", json={"rescore": False})
    assert res.status_code == 200

    assert client.get("/profile/").json()["body"] == "# Distilled\n\nnew body"


async def test_approving_with_an_edit_stores_the_edit(
    store: StateStore, client: TestClient
) -> None:
    await store.set_preference("distill_threshold", "5")
    await _rate(store, 5)
    version = client.post("/profile/review").json()["version"]

    client.post(f"/profile/review/{version}/approve",
                json={"body": "my own wording", "rescore": False})

    assert client.get("/profile/").json()["body"] == "my own wording"


async def test_approving_with_rescore_queues_the_corpus(
    store: StateStore, client: TestClient
) -> None:
    await store.set_preference("distill_threshold", "5")
    await _rate(store, 5)
    version = client.post("/profile/review").json()["version"]

    res = client.post(f"/profile/review/{version}/approve", json={"rescore": True})

    assert res.json()["rescored"] == 5


async def test_rejecting_leaves_the_profile_alone(
    store: StateStore, client: TestClient
) -> None:
    await store.set_preference("distill_threshold", "5")
    await store.create_profile_version("live", "stated", True)
    await _rate(store, 5)
    version = client.post("/profile/review").json()["version"]

    assert client.post(f"/profile/review/{version}/reject").status_code == 204

    assert client.get("/profile/").json()["body"] == "live"
    assert client.get("/profile/review").json()["state"] == "insufficient"


async def test_resolving_twice_is_a_conflict(
    store: StateStore, client: TestClient
) -> None:
    """The panel is stale; the UI should refetch."""
    await store.set_preference("distill_threshold", "5")
    await _rate(store, 5)
    version = client.post("/profile/review").json()["version"]
    client.post(f"/profile/review/{version}/approve", json={"rescore": False})

    res = client.post(f"/profile/review/{version}/reject")
    assert res.status_code == 409


def test_threshold_is_a_writable_knob(client: TestClient) -> None:
    assert client.put("/preferences/distill_threshold",
                      json={"value": 50}).status_code == 204
    assert client.get("/preferences/").json()["distill_threshold"] == 50


@pytest.mark.parametrize("value", [4, 201])
def test_threshold_outside_5_200_is_rejected(client: TestClient, value: int) -> None:
    assert client.put("/preferences/distill_threshold",
                      json={"value": value}).status_code == 400


# ---------------------------------------------------------------------------
# Beyond the mandated set: the failure paths the plan asked to be decided.
# ---------------------------------------------------------------------------

from datetime import timedelta  # noqa: E402

from services.profile import DISTILL_VERSION, DistillationError  # noqa: E402


class FailingDistiller:
    """Stands in for a model call that produced nothing usable."""

    async def propose(self, current, rated):
        raise DistillationError("the response hit max_tokens")


def _client_with(store: StateStore, distiller: object) -> TestClient:
    bus = EventBus()
    ws = WebSocketManager()
    ws.subscribe_to_bus(bus)
    return TestClient(create_app(
        event_bus=bus, ws_manager=ws, state_store=store, poller=None,
        settings=None, mount_static=False, distiller=distiller,
    ))


async def test_approving_a_blank_edit_is_rejected(
    store: StateStore, client: TestClient
) -> None:
    """`resolve_proposal` reads "" as a real edit, so it would wipe the profile.

    The state layer only distinguishes "no edit" from "an edit"; without this
    guard an approved, live, empty profile is one keystroke away, and the scorer
    would go on judging every article against nothing.
    """
    await store.set_preference("distill_threshold", "5")
    await store.create_profile_version("live", "stated", True)
    await _rate(store, 5)
    version = client.post("/profile/review").json()["version"]

    res = client.post(f"/profile/review/{version}/approve",
                      json={"body": "   ", "rescore": False})

    assert res.status_code == 400
    assert client.get("/profile/").json()["body"] == "live"
    # Still reviewable: a rejected edit must not consume the proposal.
    assert client.get("/profile/review").json()["state"] == "pending"


async def test_a_pending_proposal_outranks_a_ready_count(
    store: StateStore, client: TestClient
) -> None:
    """Pending wins over ready — the one ordering the other tests cannot see.

    Everywhere else a proposal exists, generating it has just reset the counter,
    so `count >= threshold` is false and either branch order gives `pending`.
    Rate past the threshold again and the two disagree: the reader must be shown
    the proposal awaiting them, not invited to generate a second one against the
    same base.
    """
    await store.set_preference("distill_threshold", "5")
    await _rate(store, 5)
    client.post("/profile/review")

    # Re-rated with a timestamp *after* the proposal on purpose. `_rate` stamps
    # every rating with a fixed NOW that already predates the row the proposal
    # just wrote, and the counter compares created_at — so rating again through
    # the fixture would leave the count at zero and prove nothing.
    later = datetime.now(timezone.utc) + timedelta(minutes=1)
    for row in await store.rated_articles():
        await store.rate_article(row["article_id"], -1, later)

    body = client.get("/profile/review").json()

    assert body["state"] == "pending"
    assert body["count"] == 5, "the true count, not one suppressed by the state"


def test_a_stated_profile_reports_its_kind(client: TestClient) -> None:
    version = client.put("/profile/", json={"body": "mine"}).json()["version"]

    body = client.get("/profile/").json()
    assert body["version"] == version
    assert body["kind"] == "stated"


async def test_an_approved_proposal_reports_its_kind(
    store: StateStore, client: TestClient
) -> None:
    """Editing on approval does not make it the reader's own writing."""
    await store.set_preference("distill_threshold", "5")
    await _rate(store, 5)
    version = client.post("/profile/review").json()["version"]

    client.post(f"/profile/review/{version}/approve",
                json={"body": "my own wording", "rescore": False})

    assert client.get("/profile/").json()["kind"] == "distilled"


async def test_generating_without_a_distiller_is_unavailable(
    store: StateStore
) -> None:
    """Mirrors `POST /sources/{id}/poll` with no poller: 503, not 500."""
    await store.set_preference("distill_threshold", "5")
    await _rate(store, 5)

    assert _client_with(store, None).post("/profile/review").status_code == 503


async def test_a_failed_distillation_is_a_bad_gateway(store: StateStore) -> None:
    """The request was valid; the upstream model call was not usable."""
    await store.set_preference("distill_threshold", "5")
    await _rate(store, 5)

    res = _client_with(store, FailingDistiller()).post("/profile/review")

    assert res.status_code == 502
    assert "max_tokens" in res.json()["detail"], "the panel needs a reason to show"
    assert await store.pending_proposal() is None, "nothing half-written"


async def test_a_proposal_records_the_distillation_prompt(
    store: StateStore, client: TestClient
) -> None:
    """Which prompt drafted the body stops being readable once it is edited."""
    await store.set_preference("distill_threshold", "5")
    await _rate(store, 5)
    version = client.post("/profile/review").json()["version"]

    cur = await store.db.execute(
        "SELECT distill_version FROM profile_versions WHERE version = ?", (version,)
    )
    assert (await cur.fetchone())["distill_version"] == DISTILL_VERSION


async def test_the_corpus_sent_to_the_distiller_is_capped(
    store: StateStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Newest rating first, so the slice drops the stalest signal and no other."""
    seen: list[dict] = []

    class Recorder:
        async def propose(self, current, rated):
            seen.extend(rated)
            return "# Distilled"

    monkeypatch.setattr("core.api.DISTILL_CORPUS_LIMIT", 1)
    await store.set_preference("distill_threshold", "5")
    await _rate(store, 5)

    _client_with(store, Recorder()).post("/profile/review")

    assert [row["headline"] for row in seen] == ["H4"]
