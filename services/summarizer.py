"""Article summarization via the Anthropic Messages API.

Structured output uses **forced tool use** rather than prose parsing: the
five-bullet contract is enforced by the API's schema, and re-validated by
pydantic on the way in. There is no regex, and no "please respond in JSON".

Model default is a small model per research finding R7 — small models are not
systematically less faithful than frontier ones at grounded summarization. The
scope limit of R7 applies: it measures factual consistency to the source, NOT
whether the right five bullets were chosen. Bullet selection is what the phase 1
exit criteria check by hand.

``PROMPT_VERSION`` is stored on every summary for the same reason
``rubric_version`` is stored on every score: if the prompt changes, previously
generated summaries were produced by a different function and should not be
silently treated as equivalent.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from schemas.article import Article, Summary

logger = logging.getLogger(__name__)

PROMPT_VERSION = "summary-v3"

SYSTEM_PROMPT = """\
You summarize articles for a personal reading feed. The reader wants to decide, \
in five seconds, whether to read the full piece.

Rules:
- Use ONLY facts present in the article. Never add context, background, or \
inference from your own knowledge. If the article does not say it, it does not \
go in the summary.
- The headline is your own plain-language framing of what the article is about, \
not a copy of the original title. Under 80 characters. No clickbait, no \
questions, no colons-as-drama.
- Write exactly five bullets. Each is one sentence, under 30 words, and states \
a specific claim, finding, or event from the article. Prefer concrete details \
(numbers, names, outcomes) over generalities.
- Bullets must be independently meaningful. Do not write "the author then \
explains" or otherwise refer to the article's structure.
- Keep the specifics: numbers, named entities, and the mechanism or reasoning \
behind a claim. State the actual claim rather than the fact that a claim was \
made — "X raises Y by 30% because Z" and not "the piece discusses the effect \
of X on Y".
"""

# Appended only when a vocabulary is configured — with no vocabulary the tool
# has no ``categories`` property, and instructing the model about a field it
# cannot emit is noise.
CATEGORY_PROMPT = """
Assign categories only where they genuinely apply — what the article is \
actually about, not what it mentions in passing. If nothing in the list fits, \
omit the field entirely; that is the correct answer, not a failure. Fewer \
accurate tags are better than more speculative ones.
"""

MAX_CATEGORIES = 5


def build_summary_tool(vocabulary: list[str]) -> dict[str, Any]:
    """Build the forced-use tool schema for ``vocabulary``.

    Per-instance rather than a module constant because the vocabulary is
    configuration. With an empty vocabulary the ``categories`` property is
    omitted entirely, so a deployment that has not configured categories asks
    the model for none.

    ``categories`` is never in ``required`` and carries no ``minItems``: a
    floor against a small vocabulary would make every article carry most tags,
    and a model padding to meet a quota confabulates. A wrong tag is worse than
    a missing one — it surfaces the article under a filter it does not belong to.
    """
    properties: dict[str, Any] = {
        "headline": {
            "type": "string",
            "description": "Plain-language framing, under 80 characters.",
        },
        "bullets": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 5,
            "maxItems": 5,
            "description": "Exactly five one-sentence factual bullets.",
        },
    }
    if vocabulary:
        properties["categories"] = {
            "type": "array",
            "items": {"type": "string", "enum": list(vocabulary)},
            "maxItems": MAX_CATEGORIES,
            "description": (
                "Topic categories that genuinely apply to this article. "
                "Assign only what the article is actually about — omit the field "
                "entirely rather than guessing. Fewer accurate tags are better than "
                "more speculative ones."
            ),
        }

    return {
        "name": "emit_summary",
        "description": "Emit the structured summary of the article.",
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": ["headline", "bullets"],
        },
    }


class SummarizationError(RuntimeError):
    """Raised when a summary could not be produced. Recorded as a stage error."""


class Summarizer(Protocol):
    async def summarize(self, article: Article) -> Summary: ...


class ClaudeSummarizer:
    """Summarizer backed by the Anthropic Messages API."""

    def __init__(
        self,
        client: Any,
        *,
        model: str,
        max_input_chars: int = 60_000,
        vocabulary: list[str] | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._max_input_chars = max_input_chars
        self._vocabulary = list(vocabulary or [])
        self._allowed = set(self._vocabulary)
        self._tool = build_summary_tool(self._vocabulary)
        self._system_prompt = SYSTEM_PROMPT + (
            CATEGORY_PROMPT if self._vocabulary else ""
        )

    async def summarize(self, article: Article) -> Summary:
        text = (article.text or "").strip()
        if not text:
            raise SummarizationError(
                f"article {article.id} has no text to summarize"
            )

        if len(text) > self._max_input_chars:
            logger.info(
                "summarizer: truncating article %d from %d to %d chars",
                article.id, len(text), self._max_input_chars,
            )
            text = text[: self._max_input_chars]

        user_content = (
            f"Title: {article.title}\n"
            f"Author: {article.author or 'unknown'}\n\n"
            f"{text}"
        )

        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                system=self._system_prompt,
                tools=[self._tool],
                tool_choice={"type": "tool", "name": "emit_summary"},
                messages=[{"role": "user", "content": user_content}],
            )
        except Exception as exc:
            raise SummarizationError(
                f"Anthropic API call failed for article {article.id}: {exc}"
            ) from exc

        payload = self._extract_tool_input(response, article.id)

        bullets = payload.get("bullets") or []
        if len(bullets) != 5:
            raise SummarizationError(
                f"article {article.id}: expected 5 bullets, got {len(bullets)}"
            )

        return Summary(
            headline=payload["headline"],
            bullets=bullets,
            model=self._model,
            prompt_version=PROMPT_VERSION,
            categories=self._clean_categories(payload.get("categories"), article.id),
        )

    def _clean_categories(self, raw: Any, article_id: int) -> list[str]:
        """Normalize returned tags, dropping anything outside the vocabulary.

        The schema ``enum`` is a hint the API does not enforce, so the check
        happens here — the same reason the five-bullet count is re-checked in
        Python. A bad tag is dropped and logged, never raised: the bullets are
        the product and must survive a hallucinated category. A rising drop
        rate is the signal that the vocabulary or the prompt needs work.

        A malformed field — anything that is not a list, including a bare
        scalar — is the same signal and gets the same treatment. Iterating it
        would raise (or, for a string, silently yield characters), and no shape
        of tag is allowed to cost the article its bullets.
        """
        if raw is None:
            return []
        if not isinstance(raw, list):
            logger.info(
                "summarizer: article %d ignoring malformed categories field: %r",
                article_id, raw,
            )
            return []

        cleaned: list[str] = []
        for value in raw:
            category = str(value).strip().lower()
            if category not in self._allowed:
                logger.info(
                    "summarizer: article %d dropping category outside vocabulary: %r",
                    article_id, value,
                )
                continue
            if category not in cleaned:
                cleaned.append(category)
        return cleaned[:MAX_CATEGORIES]

    @staticmethod
    def _extract_tool_input(response: Any, article_id: int) -> dict[str, Any]:
        for block in getattr(response, "content", []):
            if getattr(block, "type", None) == "tool_use":
                return dict(block.input)
        raise SummarizationError(
            f"article {article_id}: response contained no tool_use block"
        )
