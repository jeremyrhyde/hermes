"""Scoring: rubric versioning, tool schema, range validation, stamping."""

from __future__ import annotations

import pytest

from services.rubric import RUBRIC, RUBRIC_VERSION


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


from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone

from schemas.article import Article, Summary
from schemas.scoring import Score
from services.scorer import SCORE_TOOL, ClaudeScorer, ScoringError

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


def _scorer(client) -> ClaudeScorer:
    return ClaudeScorer(client, model="m", profile_body="I like X",
                        profile_version="profile-v1")


def test_score_tool_bounds_the_range() -> None:
    props = SCORE_TOOL["input_schema"]["properties"]
    assert props["score"]["minimum"] == 0 and props["score"]["maximum"] == 100
    assert set(SCORE_TOOL["input_schema"]["required"]) == {"score", "rationale"}


async def test_scoring_stamps_both_versions() -> None:
    """A profile update must never be mistaken for a rubric change."""
    scorer = _scorer(_client({"score": 82, "rationale": "ai x markets"}))

    result = await scorer.score(ARTICLE, SUMMARY)

    assert result.value == 82
    assert result.rubric_version == RUBRIC_VERSION
    assert result.profile_version == "profile-v1"


async def test_scoring_is_deterministic_by_construction() -> None:
    """R5: prompt sensitivity is bad enough without sampling noise on top."""
    client = _client({"score": 50, "rationale": "r"})
    await _scorer(client).score(ARTICLE, SUMMARY)

    kwargs = client.messages.create.await_args.kwargs
    assert kwargs["temperature"] == 0
    assert kwargs["tool_choice"] == {"type": "tool", "name": "emit_score"}


async def test_scoring_does_not_send_the_article_text() -> None:
    """The summary is the decision surface, and 10x cheaper."""
    client = _client({"score": 50, "rationale": "r"})
    await _scorer(client).score(ARTICLE, SUMMARY)

    sent = str(client.messages.create.await_args.kwargs["messages"])
    assert "body text" not in sent
    assert "Export controls compress margins" in sent


async def test_scoring_sends_the_profile_independent_context() -> None:
    client = _client({"score": 50, "rationale": "r"})
    await _scorer(client).score(ARTICLE, SUMMARY)

    sent = str(client.messages.create.await_args.kwargs["messages"])
    for expected in ("Export controls and margins", "ai", "markets", "1200"):
        assert expected in sent


async def test_signals_record_what_the_model_saw() -> None:
    """Makes the summary-bottleneck question answerable later."""
    scorer = _scorer(_client({"score": 50, "rationale": "r"}))

    result = await scorer.score(ARTICLE, SUMMARY)

    assert "bullets" in result.signals["inputs"]
    assert result.signals["summary_prompt_version"] == "summary-v3"


@pytest.mark.parametrize("bad", [-1, 101, 1000])
async def test_out_of_range_scores_are_rejected(bad: int) -> None:
    """The schema bound is a hint the API does not enforce."""
    scorer = _scorer(_client({"score": bad, "rationale": "r"}))

    with pytest.raises(ScoringError):
        await scorer.score(ARTICLE, SUMMARY)


async def test_a_missing_rationale_is_rejected() -> None:
    """Without it a wrong score is not diagnosable."""
    scorer = _scorer(_client({"score": 70, "rationale": "   "}))

    with pytest.raises(ScoringError):
        await scorer.score(ARTICLE, SUMMARY)


async def test_api_failure_becomes_a_scoring_error() -> None:
    client = MagicMock()
    client.messages.create = AsyncMock(side_effect=RuntimeError("boom"))

    with pytest.raises(ScoringError):
        await _scorer(client).score(ARTICLE, SUMMARY)
