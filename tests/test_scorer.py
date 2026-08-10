"""Scoring: rubric versioning, tool schema, range validation, stamping."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from schemas.article import Article, Summary
from schemas.scoring import Score
from services.rubric import RUBRIC, RUBRIC_VERSION
from services.scorer import SCORE_TOOL, ClaudeScorer, Profile, ScoringError


def test_rubric_version_is_pinned() -> None:
    assert RUBRIC_VERSION == "rubric-v1"


def test_rubric_demands_lens_interaction_not_mere_breadth() -> None:
    """Section 2.6: naive multi-domain scoring ranks a link roundup first."""
    text = RUBRIC.lower()
    assert "interact" in text or "changes the conclusion" in text


def test_rubric_requires_the_rationale_to_name_the_lenses() -> None:
    """Section 11.5: the only defense against a fabricated interaction."""
    assert "rationale" in RUBRIC.lower()


def test_rubric_is_a_frozen_constant_not_a_template() -> None:
    """No runtime interpolation: an edit must be a git diff and a version bump."""
    assert "{" not in RUBRIC and "}" not in RUBRIC


NOW = datetime(2026, 8, 9, tzinfo=timezone.utc)

ARTICLE = Article(
    id=1, source_id="acx", guid="g", canonical_url="https://x/1",
    title="Export controls and margins", author=None, published_at=NOW,
    fetched_at=NOW, text="body text", word_count=1200,
)
SUMMARY = Summary(
    headline="Export controls compress margins",
    bullets=[f"point {i}" for i in range(5)],
    model="m", prompt_version="summary-v3", categories=["ai", "markets"],
)


def _client(payload: dict) -> MagicMock:
    block = MagicMock()
    block.type = "tool_use"
    block.name = "emit_score"
    block.input = payload
    response = MagicMock()
    response.content = [block]
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=response)
    return client


PROFILE = Profile(version="profile-v1", body="I like X")


def _scorer(client) -> ClaudeScorer:
    return ClaudeScorer(client, model="m")


def test_score_tool_bounds_the_range() -> None:
    props = SCORE_TOOL["input_schema"]["properties"]
    assert props["score"]["minimum"] == 0 and props["score"]["maximum"] == 100
    assert set(SCORE_TOOL["input_schema"]["required"]) == {"score", "rationale"}


async def test_scoring_stamps_both_versions() -> None:
    """A profile update must never be mistaken for a rubric change."""
    scorer = _scorer(_client({"score": 82, "rationale": "ai x markets"}))

    result = await scorer.score(ARTICLE, SUMMARY, PROFILE)

    assert result.value == 82
    assert result.rubric_version == RUBRIC_VERSION
    assert result.profile_version == "profile-v1"


async def test_scoring_never_sends_a_sampling_parameter() -> None:
    """Claude 5 models reject temperature/top_p/top_k with a 400.

    This is a live-API contract no mock can enforce, so it is asserted on the
    outgoing call instead: every one of these parameters was a 400 in the
    end-to-end run that found this, and the suite was green throughout because
    every scorer test mocks the client.
    """
    client = _client({"score": 50, "rationale": "r"})
    await _scorer(client).score(ARTICLE, SUMMARY, PROFILE)

    kwargs = client.messages.create.await_args.kwargs
    for banned in ("temperature", "top_p", "top_k"):
        assert banned not in kwargs, f"{banned} is rejected by Claude 5 models"


async def test_scoring_forces_the_tool_and_pins_effort() -> None:
    """Effort replaces temperature as the knob, so it is set, not defaulted."""
    client = _client({"score": 50, "rationale": "r"})
    await _scorer(client).score(ARTICLE, SUMMARY, PROFILE)

    kwargs = client.messages.create.await_args.kwargs
    assert kwargs["tool_choice"] == {"type": "tool", "name": "emit_score"}
    assert kwargs["output_config"] == {"effort": "high"}


async def test_max_tokens_leaves_room_for_thinking() -> None:
    """Adaptive thinking is on by default and shares the max_tokens budget.

    Sized for the score plus a rationale alone, a turn truncates inside the
    thinking that precedes it.
    """
    client = _client({"score": 50, "rationale": "r"})
    await _scorer(client).score(ARTICLE, SUMMARY, PROFILE)

    assert client.messages.create.await_args.kwargs["max_tokens"] >= 4096


async def test_signals_record_the_model_and_effort() -> None:
    """Both change scores, so both belong in the row that explains one."""
    result = await _scorer(_client({"score": 50, "rationale": "r"})).score(
        ARTICLE, SUMMARY, PROFILE
    )

    assert result.signals["model"] == "m"
    assert result.signals["effort"] == "high"


async def test_scoring_does_not_send_the_article_text() -> None:
    """The summary is the decision surface, and 10x cheaper.

    Asserted against the *whole* call, not just ``messages``. Checking only
    the user turn would stay green if a refactor moved article context into
    the system prompt — leaving the test passing while the property it is
    named for was broken.
    """
    client = _client({"score": 50, "rationale": "r"})
    await _scorer(client).score(ARTICLE, SUMMARY, PROFILE)

    whole_call = str(client.messages.create.await_args)
    assert ARTICLE.text not in whole_call
    assert "Export controls compress margins" in whole_call


async def test_scoring_sends_the_profile_independent_context() -> None:
    """The evidence the model judges, and the proof it is profile-free.

    The name promises independence, so the test asserts it: the profile must
    reach the model as *judgment* in the system prompt and never contaminate
    the user turn, which is the evidence. Without the negative assertion this
    was only a field-presence check wearing a stronger name.
    """
    client = _client({"score": 50, "rationale": "r"})
    await _scorer(client).score(ARTICLE, SUMMARY, PROFILE)

    sent = str(client.messages.create.await_args.kwargs["messages"])
    for expected in ("Export controls and margins", "Categories: ai, markets", "1200"):
        assert expected in sent
    assert "I like X" not in sent, "the profile belongs in the system prompt only"


async def test_signals_record_what_the_model_saw() -> None:
    """Makes the summary-bottleneck question answerable later."""
    scorer = _scorer(_client({"score": 50, "rationale": "r"}))

    result = await scorer.score(ARTICLE, SUMMARY, PROFILE)

    assert "bullets" in result.signals["inputs"]
    assert result.signals["summary_prompt_version"] == "summary-v3"


@pytest.mark.parametrize("bad", [-1, 101, 1000])
async def test_out_of_range_scores_are_rejected(bad: int) -> None:
    """The schema bound is a hint the API does not enforce."""
    scorer = _scorer(_client({"score": bad, "rationale": "r"}))

    with pytest.raises(ScoringError):
        await scorer.score(ARTICLE, SUMMARY, PROFILE)


async def test_a_missing_rationale_is_rejected() -> None:
    """Without it a wrong score is not diagnosable."""
    scorer = _scorer(_client({"score": 70, "rationale": "   "}))

    with pytest.raises(ScoringError):
        await scorer.score(ARTICLE, SUMMARY, PROFILE)


async def test_api_failure_becomes_a_scoring_error() -> None:
    client = MagicMock()
    client.messages.create = AsyncMock(side_effect=RuntimeError("boom"))

    with pytest.raises(ScoringError):
        await _scorer(client).score(ARTICLE, SUMMARY, PROFILE)


@pytest.mark.parametrize("bad", [True, False, "82", 82.0, None])
async def test_non_integer_scores_are_rejected(bad: object) -> None:
    """The subtlest line in the scorer, and the easiest to delete by accident.

    ``isinstance(True, int)`` is True in Python, so without an explicit bool
    guard a payload of ``{"score": true}`` becomes a perfectly valid score of
    1 — a fabricated judgment that no range check would ever catch.
    """
    scorer = _scorer(_client({"score": bad, "rationale": "r"}))

    with pytest.raises(ScoringError):
        await scorer.score(ARTICLE, SUMMARY, PROFILE)


# ---------------------------------------------------------------------------
# The profile is an argument, not construction state
#
# Before phase 4 the profile was baked into the system prompt in __init__, so a
# profile the reader approved at runtime reached nothing: the process kept
# scoring against whatever was live at boot while stamping the boot version.
# ---------------------------------------------------------------------------


async def test_the_body_sent_is_the_body_of_the_version_stamped() -> None:
    """The two halves of a profile must not be able to disagree.

    They arrive as one value and are read from it in one place, so a score
    stamped ``profile-v7`` was necessarily judged against v7's text.
    """
    client = _client({"score": 50, "rationale": "r"})
    profile = Profile(version="profile-v7", body="I like octopuses")

    result = await _scorer(client).score(ARTICLE, SUMMARY, profile)

    system = client.messages.create.await_args.kwargs["system"]
    assert profile.body in system
    assert profile.version in system
    assert result.profile_version == profile.version


async def test_one_scorer_follows_the_profile_it_is_handed() -> None:
    """A newly approved profile takes effect with no restart and no rebuild.

    Same scorer object, two profiles, two different system prompts and stamps —
    the property the frozen ``self._system_prompt`` made impossible.
    """
    client = _client({"score": 50, "rationale": "r"})
    scorer = _scorer(client)

    first = await scorer.score(ARTICLE, SUMMARY, Profile("profile-v1", "old taste"))
    old_system = client.messages.create.await_args.kwargs["system"]
    second = await scorer.score(ARTICLE, SUMMARY, Profile("profile-v2", "new taste"))
    new_system = client.messages.create.await_args.kwargs["system"]

    assert first.profile_version == "profile-v1"
    assert second.profile_version == "profile-v2"
    assert "old taste" in old_system and "old taste" not in new_system
    assert "new taste" in new_system


def test_the_scorer_keeps_no_profile_of_its_own() -> None:
    """Nothing to go stale: there is no profile state to refresh or forget to."""
    scorer = _scorer(_client({"score": 50, "rationale": "r"}))

    assert not [name for name in vars(scorer) if "profile" in name]


async def test_the_rubric_still_precedes_the_profile() -> None:
    """Instructions, then the material they operate on — unchanged by phase 4.

    Assembling the prompt per call rather than per scorer is the only thing
    that moved; the order it assembles in is load-bearing and did not.
    """
    client = _client({"score": 50, "rationale": "r"})
    await _scorer(client).score(ARTICLE, SUMMARY, PROFILE)

    system = client.messages.create.await_args.kwargs["system"]
    assert system.index(RUBRIC) < system.index(PROFILE.body)


# ---------------------------------------------------------------------------
# Score, at the schema level
#
# Ported from tests/test_schemas_feed.py, which exercised the now-deleted
# schemas.article.Score. That version asserted only that signals defaulted to
# {} and that a dict round-tripped — it never tested the 0-100 bounds it was
# credited with covering, and its optional `rationale` could not express the
# constraint spec 12.4 actually requires.
# ---------------------------------------------------------------------------


def test_score_defaults_signals_and_metadata_to_empty() -> None:
    s = Score(value=74, rationale="why", rubric_version="v1", profile_version="p1")
    assert s.signals == {} and s.metadata == {}


def test_score_round_trips_arbitrary_signals() -> None:
    """Spec 12.4: a score is decomposable, not an opaque number."""
    s = Score(value=74, rationale="why", rubric_version="v1", profile_version="p1",
              signals={"recency": 0.4, "topic_match": 0.9})
    assert s.signals["topic_match"] == 0.9


@pytest.mark.parametrize("bad", [-1, 101])
def test_score_bounds_are_enforced_by_the_schema(bad: int) -> None:
    """Defence in depth: the scorer checks this too, on untrusted model output."""
    with pytest.raises(ValidationError):
        Score(value=bad, rationale="why", rubric_version="v1", profile_version="p1")


def test_score_requires_a_rationale() -> None:
    """Spec 12.4 declares rationale required; the phase-1 model made it optional.

    This is the constraint that makes a wrong score diagnosable rather than
    merely wrong, so it belongs in the type, not only in the scorer.
    """
    with pytest.raises(ValidationError):
        Score(value=74, rubric_version="v1", profile_version="p1")
