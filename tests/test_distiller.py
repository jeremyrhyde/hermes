"""Distillation: prompt shape, tool schema, validation."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.profile import (
    DISTILL_PROMPT,
    DISTILL_TOOL,
    DISTILL_VERSION,
    DistillationError,
    ProfileDistiller,
)

RATED = [
    {"headline": "Export controls compress margins", "bullets": ["b1", "b2"],
     "categories": ["ai", "markets"], "score": 82, "rating": 1},
    {"headline": "Open Thread 445", "bullets": ["c1"],
     "categories": [], "score": 5, "rating": -1},
]


def _client(payload: dict) -> MagicMock:
    block = MagicMock()
    block.type = "tool_use"
    block.name = "emit_profile"
    block.input = payload
    response = MagicMock()
    response.content = [block]
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=response)
    return client


def _distiller(client) -> ProfileDistiller:
    return ProfileDistiller(client, model="m")


def test_distill_version_is_pinned() -> None:
    assert DISTILL_VERSION == "distill-v1"


def test_prompt_is_a_frozen_constant() -> None:
    """An edit must be a git diff and a version bump, as with the rubric."""
    assert "{" not in DISTILL_PROMPT and "}" not in DISTILL_PROMPT


def test_prompt_asks_to_preserve_the_stated_criteria() -> None:
    """Section 2.9: a soft constraint the review gate enforces."""
    assert "stated" in DISTILL_PROMPT.lower()


def test_tool_requires_a_profile_body() -> None:
    assert DISTILL_TOOL["input_schema"]["required"] == ["profile"]


async def test_proposal_returns_the_emitted_body() -> None:
    distiller = _distiller(_client({"profile": "# Taste profile\n\nNew body."}))

    assert await distiller.propose("old body", RATED) == "# Taste profile\n\nNew body."


async def test_proposal_never_sends_a_sampling_parameter() -> None:
    """Claude 5 models reject temperature/top_p/top_k with a 400.

    A live-API contract no mock can enforce, so it is asserted on the outgoing
    call — this exact defect shipped in phase 3 with the suite green.
    """
    client = _client({"profile": "body"})
    await _distiller(client).propose("old", RATED)

    kwargs = client.messages.create.await_args.kwargs
    for banned in ("temperature", "top_p", "top_k"):
        assert banned not in kwargs


async def test_proposal_forces_the_tool() -> None:
    client = _client({"profile": "body"})
    await _distiller(client).propose("old", RATED)

    kwargs = client.messages.create.await_args.kwargs
    assert kwargs["tool_choice"] == {"type": "tool", "name": "emit_profile"}


async def test_proposal_sends_the_current_profile_and_the_ratings() -> None:
    client = _client({"profile": "body"})
    await _distiller(client).propose("MY CURRENT PROFILE", RATED)

    sent = str(client.messages.create.await_args)
    assert "MY CURRENT PROFILE" in sent
    assert "Export controls compress margins" in sent
    assert "Open Thread 445" in sent


async def test_proposal_sends_the_rating_direction() -> None:
    """A body that ignored which way the reader voted would be worthless."""
    client = _client({"profile": "body"})
    await _distiller(client).propose("old", RATED)

    messages = str(client.messages.create.await_args.kwargs["messages"])
    assert "+1" in messages or "up" in messages.lower()
    assert "-1" in messages or "down" in messages.lower()


@pytest.mark.parametrize("bad", ["", "   ", None])
async def test_an_empty_proposal_is_rejected(bad) -> None:
    """A blank profile would score every article against nothing."""
    distiller = _distiller(_client({"profile": bad}))

    with pytest.raises(DistillationError):
        await distiller.propose("old", RATED)


async def test_proposal_sends_the_tool_it_forces() -> None:
    """A dropped `tools` or a drifted name passes every other test and 400s live.

    The same class of defect as the phase-3 sampling parameter: a live-API
    contract, so it is asserted on the outgoing call.
    """
    client = _client({"profile": "body"})
    await _distiller(client).propose("old", RATED)

    kwargs = client.messages.create.await_args.kwargs
    assert kwargs["tools"] == [DISTILL_TOOL]
    assert kwargs["tool_choice"]["name"] == DISTILL_TOOL["name"]


async def test_a_truncated_response_is_rejected() -> None:
    """A body cut off mid-sentence is indistinguishable from a short one."""
    client = _client({"profile": "half a prof"})
    client.messages.create.return_value.stop_reason = "max_tokens"

    with pytest.raises(DistillationError):
        await _distiller(client).propose("old", RATED)


@pytest.mark.parametrize("bad", [None, 0, 2, "1"])
async def test_an_unexpected_rating_is_rejected(bad) -> None:
    """Guessing the direction would invert the reader's verdict silently."""
    rated = [{"headline": "h", "bullets": [], "categories": [],
              "score": 50, "rating": bad}]

    with pytest.raises(DistillationError):
        await _distiller(_client({"profile": "body"})).propose("old", rated)


async def test_a_rating_error_is_not_reported_as_an_api_failure() -> None:
    """The message reaches the reader's review panel, so it must not misdirect.

    `_build_context` is evaluated as an argument to the API call, so it runs
    inside the same `try`, and the broad handler would re-wrap a corrupt rating
    as "Anthropic API call failed" — sending the reader to look at the network
    for a problem in their own data.
    """
    rated = [{"headline": "h", "bullets": [], "categories": [],
              "score": 50, "rating": 0}]

    with pytest.raises(DistillationError) as excinfo:
        await _distiller(_client({"profile": "body"})).propose("old", rated)

    assert "unexpected rating" in str(excinfo.value)
    assert "API call failed" not in str(excinfo.value)


async def test_a_missing_headline_is_not_sent_as_the_string_none() -> None:
    client = _client({"profile": "body"})
    await _distiller(client).propose(
        "old",
        [{"bullets": [], "categories": [], "score": 50, "rating": -1}],
    )

    messages = str(client.messages.create.await_args.kwargs["messages"])
    assert "None" not in messages


async def test_an_unscored_article_is_not_sent_as_the_string_none() -> None:
    """`rated_articles()` returns score=None for a rated-but-unscored article.

    Formatted bare it reaches the prompt as "None", which reads as a value.
    """
    client = _client({"profile": "body"})
    unscored = [{"headline": "h", "bullets": [], "categories": [],
                 "score": None, "rating": 1}]
    await _distiller(client).propose("old", unscored)

    messages = str(client.messages.create.await_args.kwargs["messages"])
    assert "None" not in messages


async def test_api_failure_becomes_a_distillation_error() -> None:
    client = MagicMock()
    client.messages.create = AsyncMock(side_effect=RuntimeError("boom"))

    with pytest.raises(DistillationError):
        await _distiller(client).propose("old", RATED)


async def test_proposing_from_no_ratings_is_rejected() -> None:
    """There is nothing to distill; the threshold should have prevented this."""
    with pytest.raises(DistillationError):
        await _distiller(_client({"profile": "body"})).propose("old", [])


async def test_no_ratings_is_rejected_without_calling_the_api() -> None:
    """The given test above passes either side of the call; pin which."""
    client = _client({"profile": "body"})

    with pytest.raises(DistillationError):
        await _distiller(client).propose("old", [])

    client.messages.create.assert_not_awaited()


@pytest.mark.parametrize("bad", [[1, 2], "a string", 5, None])
async def test_malformed_tool_arguments_stay_a_distillation_error(bad) -> None:
    """`dict()` on a non-mapping raises TypeError/ValueError, not our error.

    The route catches DistillationError to return a 502 the panel can show; an
    escaping TypeError would be a 500 with a traceback instead.
    """
    with pytest.raises(DistillationError):
        await _distiller(_client(bad)).propose("old", RATED)
