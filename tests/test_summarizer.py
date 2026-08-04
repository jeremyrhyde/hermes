"""Summarizer: forced tool use, exactly five bullets, no network."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from schemas.article import Article
from services.summarizer import (
    PROMPT_VERSION,
    ClaudeSummarizer,
    SummarizationError,
)
from tests.conftest import make_article


@dataclass
class FakeBlock:
    type: str
    name: str
    input: dict[str, Any]


@dataclass
class FakeResponse:
    content: list[FakeBlock]


class FakeMessages:
    def __init__(self, response: Any) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class FakeClient:
    def __init__(self, response: Any) -> None:
        self.messages = FakeMessages(response)


def _ok_response(bullets: list[str] | None = None) -> FakeResponse:
    return FakeResponse(
        content=[
            FakeBlock(
                type="tool_use",
                name="emit_summary",
                input={
                    "headline": "A tidy headline",
                    "bullets": bullets or [f"point {i}" for i in range(5)],
                },
            )
        ]
    )


async def test_returns_headline_and_five_bullets() -> None:
    client = FakeClient(_ok_response())
    summarizer = ClaudeSummarizer(client, model="claude-haiku-4-5")

    summary = await summarizer.summarize(make_article(text="Body text."))

    assert summary.headline == "A tidy headline"
    assert len(summary.bullets) == 5
    assert summary.model == "claude-haiku-4-5"
    assert summary.prompt_version == PROMPT_VERSION


async def test_forces_the_tool_so_output_is_structured() -> None:
    client = FakeClient(_ok_response())
    await ClaudeSummarizer(client, model="m").summarize(make_article(text="B"))

    kwargs = client.messages.calls[0]
    assert kwargs["tool_choice"] == {"type": "tool", "name": "emit_summary"}
    schema = kwargs["tools"][0]["input_schema"]["properties"]["bullets"]
    assert schema["minItems"] == 5
    assert schema["maxItems"] == 5


async def test_wrong_bullet_count_raises() -> None:
    """Model ignored the schema — surface it rather than storing a bad summary."""
    client = FakeClient(_ok_response(bullets=["only", "three", "here"]))

    with pytest.raises(SummarizationError, match="bullets"):
        await ClaudeSummarizer(client, model="m").summarize(make_article(text="B"))


async def test_missing_tool_block_raises() -> None:
    client = FakeClient(FakeResponse(content=[]))

    with pytest.raises(SummarizationError, match="tool_use"):
        await ClaudeSummarizer(client, model="m").summarize(make_article(text="B"))


async def test_api_error_is_wrapped() -> None:
    client = FakeClient(RuntimeError("connection reset"))

    with pytest.raises(SummarizationError, match="connection reset"):
        await ClaudeSummarizer(client, model="m").summarize(make_article(text="B"))


async def test_empty_text_raises_without_calling_the_api() -> None:
    client = FakeClient(_ok_response())

    with pytest.raises(SummarizationError, match="no text"):
        await ClaudeSummarizer(client, model="m").summarize(make_article(text=""))

    assert client.messages.calls == []


async def test_long_text_is_truncated_before_sending() -> None:
    client = FakeClient(_ok_response())
    summarizer = ClaudeSummarizer(client, model="m", max_input_chars=100)

    await summarizer.summarize(make_article(text="x" * 5000))

    sent = client.messages.calls[0]["messages"][0]["content"]
    assert len(sent) < 500
