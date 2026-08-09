"""Article scoring via the Anthropic Messages API.

Sibling of :mod:`services.summarizer`, and deliberately the same shape: forced
tool use rather than prose parsing, a ``Protocol`` so the pipeline depends on
the capability and not on Anthropic, and re-validation in Python of everything
the tool schema merely *asks* for.

Two things differ from summarization, and both follow from what a score is.

**The input is the summary, not the article.** The article text is roughly ten
times the tokens, and the summary is the surface the reader actually acts on —
scoring the text would score something the reader never sees. Every input is
profile-independent: headline, bullets, title, source, categories, publication
date, word count. The profile touches judgment, never evidence, so a profile
edit can change the verdict but never the facts the verdict was reached from.

**A bad response is fatal, not droppable.** The summarizer drops a hallucinated
category and keeps the bullets, because the bullets are the product. Here the
score *is* the product: an out-of-range number or an empty rationale leaves
nothing to carry on with, so it raises.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from schemas.article import Article, Summary
from schemas.scoring import Score
from services.rubric import RUBRIC, RUBRIC_VERSION

logger = logging.getLogger(__name__)

# The bounds are declared here *and* checked after the call. A JSON-schema
# bound is a hint the API does not enforce — the same lesson the category
# ``enum`` taught in phase 1.
SCORE_TOOL: dict[str, Any] = {
    "name": "emit_score",
    "description": "Emit the score and its rationale for this article.",
    "input_schema": {
        "type": "object",
        "properties": {
            "score": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100,
                "description": "Fit against the reader's profile, 0-100.",
            },
            "rationale": {
                "type": "string",
                "description": (
                    "A few sentences naming each lens found and the specific "
                    "interaction between them, plus any penalty applied."
                ),
            },
        },
        "required": ["score", "rationale"],
    },
}

# What the model was shown. Stored on every score so a later "were the
# summaries the bottleneck?" is a query rather than a memory exercise.
SCORING_INPUTS = [
    "headline",
    "bullets",
    "title",
    "source",
    "categories",
    "published_at",
    "word_count",
]


class ScoringError(RuntimeError):
    """Raised when a score could not be produced. Recorded as a stage error."""


class Scorer(Protocol):
    async def score(self, article: Article, summary: Summary) -> Score: ...


class ClaudeScorer:
    """Scorer backed by the Anthropic Messages API."""

    def __init__(
        self,
        client: Any,
        *,
        model: str,
        profile_body: str,
        profile_version: str,
        rubric: str = RUBRIC,
        rubric_version: str = RUBRIC_VERSION,
    ) -> None:
        self._client = client
        self._model = model
        self._profile_version = profile_version
        self._rubric_version = rubric_version
        # Rubric first, profile second: the rubric says how to apply the
        # profile, so it reads as instructions followed by the material they
        # operate on. Both are system content — they are the scoring function,
        # constant across every article, and nothing about the article belongs
        # beside them.
        self._system_prompt = (
            f"{rubric}\n\nREADER PROFILE (version {profile_version}).\n{profile_body}"
        )

    async def score(self, article: Article, summary: Summary) -> Score:
        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                # R5: a cosmetic prompt edit already flips 16-24% of scores at
                # temperature 0. Sampling noise on top of that would make a
                # score change unattributable to anything.
                temperature=0,
                system=self._system_prompt,
                tools=[SCORE_TOOL],
                tool_choice={"type": "tool", "name": "emit_score"},
                messages=[
                    {"role": "user", "content": self._build_context(article, summary)}
                ],
            )
        except Exception as exc:
            raise ScoringError(
                f"Anthropic API call failed for article {article.id}: {exc}"
            ) from exc

        payload = self._extract_tool_input(response, article.id)

        return Score(
            value=self._clean_value(payload.get("score"), article.id),
            rationale=self._clean_rationale(payload.get("rationale"), article.id),
            rubric_version=self._rubric_version,
            profile_version=self._profile_version,
            signals={
                "inputs": list(SCORING_INPUTS),
                "summary_prompt_version": summary.prompt_version,
            },
        )

    @staticmethod
    def _build_context(article: Article, summary: Summary) -> str:
        """Assemble the article context the model judges.

        The article's ``text`` is deliberately absent — see the module
        docstring. Every field here is one the reader would see on the card,
        plus the two the card implies: how long the piece is, and how old.
        """
        return (
            f"Headline: {summary.headline}\n"
            "Bullets:\n"
            + "".join(f"- {bullet}\n" for bullet in summary.bullets)
            + f"\nOriginal title: {article.title}\n"
            f"Source: {article.source_id}\n"
            f"Categories: {', '.join(summary.categories) or 'none'}\n"
            f"Published: {article.published_at.isoformat() if article.published_at else 'unknown'}\n"
            f"Word count: {article.word_count if article.word_count is not None else 'unknown'}\n"
        )

    @staticmethod
    def _clean_value(raw: Any, article_id: int) -> int:
        """Re-check the range the schema only declared.

        A non-integer is the same failure as an out-of-range one: the model did
        not answer the question asked, and there is no partial score to keep.
        """
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ScoringError(
                f"article {article_id}: score was not an integer: {raw!r}"
            )
        if not 0 <= raw <= 100:
            raise ScoringError(
                f"article {article_id}: score {raw} is outside 0-100"
            )
        return raw

    @staticmethod
    def _clean_rationale(raw: Any, article_id: int) -> str:
        """Require a non-blank rationale.

        Whitespace is treated as absence: a blank rationale satisfies the
        schema's ``required`` while leaving a wrong score undiagnosable, which
        is exactly the failure the field exists to prevent.
        """
        rationale = str(raw or "").strip()
        if not rationale:
            raise ScoringError(f"article {article_id}: score had no rationale")
        return rationale

    @staticmethod
    def _extract_tool_input(response: Any, article_id: int) -> dict[str, Any]:
        for block in getattr(response, "content", []):
            if getattr(block, "type", None) == "tool_use":
                return dict(block.input)
        raise ScoringError(
            f"article {article_id}: response contained no tool_use block"
        )
