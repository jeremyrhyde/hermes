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

**The profile is an argument, not state.** It is the one half of the scoring
function that changes while the process runs: the reader approves a distilled
profile and the live one moves. A scorer that captured it at construction went
on judging against the profile the process booted with — and stamping that
version onto rows produced long after — until someone restarted. It arrives per
call instead, as a :class:`Profile` carrying the body and its version together
so a score can never name a profile it was not judged against.

**A bad response is fatal, not droppable.** The summarizer drops a hallucinated
category and keeps the bullets, because the bullets are the product. Here the
score *is* the product: an out-of-range number or an empty rationale leaves
nothing to carry on with, so it raises.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from schemas.article import Article, Summary
from schemas.scoring import Score
from services.rubric import RUBRIC, RUBRIC_VERSION

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


@dataclass(frozen=True)
class Profile:
    """The taste profile a score was judged against: body and version, together.

    One value rather than two arguments, because the failure this type exists to
    prevent is exactly the two disagreeing. Phase 4 made the live profile a
    moving target — the reader approves a distillation and the newest approved
    row changes — so a scorer that took the body and the version separately
    could be handed a fresh body with a stale stamp, producing rows that name a
    profile they were not judged against. ``profile_version`` is the column
    phase 4's before/after comparison is keyed on, so a wrong stamp is worse
    than a missing score: the numbers look right and the attribution is wrong.

    Built from :meth:`from_state` in production, off a single row, so the pair
    is never assembled from two reads taken at different times. A convention,
    not a guarantee: this is a plain frozen dataclass with a public constructor,
    and tests build one directly. Drift here is unconstructed, not
    unrepresentable.
    """

    version: str
    body: str

    @classmethod
    def from_state(cls, pair: tuple[str, str] | None) -> Profile | None:
        """Adapt :meth:`core.state.StateStore.latest_profile`'s return.

        ``None`` in means no approved profile exists, which is not an error:
        scoring is skipped and the article stays readable and unscored.

        Unpacked rather than indexed: this is the seam where an outside shape
        becomes an internal type, so a wrong-arity tuple should raise a legible
        ``ValueError`` here rather than an ``IndexError`` from inside the call.
        """

        if pair is None:
            return None
        version, body = pair
        return cls(version=version, body=body)


class Scorer(Protocol):
    async def score(
        self, article: Article, summary: Summary, profile: Profile
    ) -> Score: ...


class ClaudeScorer:
    """Scorer backed by the Anthropic Messages API."""

    def __init__(
        self,
        client: Any,
        *,
        model: str,
        rubric: str = RUBRIC,
        rubric_version: str = RUBRIC_VERSION,
        effort: str = "high",
    ) -> None:
        self._client = client
        self._model = model
        # Set explicitly rather than left to the API default, and recorded on
        # every score. Effort changes what the model produces, so a silent
        # change to the default would move scores with nothing in the row to
        # explain why — the same reason the rubric and profile are versioned.
        self._effort = effort
        self._rubric = rubric
        self._rubric_version = rubric_version
        # No profile is held here. The rubric is frozen at import and versioned
        # by a git diff, so caching it is safe; the profile changes underneath a
        # running process every time the reader approves a distillation, and a
        # scorer that captured one at construction went on judging against it
        # forever — the process could only be corrected by a restart. It arrives
        # per call instead, from whoever knows when a run begins.

    async def score(
        self, article: Article, summary: Summary, profile: Profile
    ) -> Score:
        try:
            response = await self._client.messages.create(
                model=self._model,
                # Caps thinking *and* response together. Adaptive thinking is on
                # by default on Claude 5 models, so a budget sized for the score
                # and a few sentences of rationale truncates mid-answer.
                max_tokens=4096,
                # Was `temperature=0`, for the determinism R5 argues for. Claude
                # 5 models reject `temperature`, `top_p`, and `top_k` outright
                # with a 400 — the parameter is gone, not merely discouraged.
                #
                # Nothing replaces it. Effort bounds how hard the model thinks,
                # not how much it samples, so scoring is no longer reproducible
                # by construction and two runs over one article may differ. R5's
                # concern stands and its remedy does not: the rubric being
                # frozen and versioned is now the whole of the defense.
                output_config={"effort": self._effort},
                system=self._build_system_prompt(profile),
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
            # From the same value whose body was sent above, so the stamp and
            # the text it names cannot come apart.
            profile_version=profile.version,
            signals={
                "inputs": list(SCORING_INPUTS),
                "summary_prompt_version": summary.prompt_version,
                "model": self._model,
                "effort": self._effort,
            },
        )

    def _build_system_prompt(self, profile: Profile) -> str:
        """Assemble the scoring function: rubric, then the profile it applies.

        Rubric first, profile second: the rubric says how to apply the profile,
        so it reads as instructions followed by the material they operate on.
        Both are system content — they are constant across every article in a
        run, and nothing about the article belongs beside them.

        Per call rather than per scorer. Formatting two strings costs nothing
        next to the API call it precedes, and doing it here is what lets a
        profile approved five minutes ago reach the very next score.
        """

        return (
            f"{self._rubric}\n\nREADER PROFILE (version {profile.version}).\n"
            f"{profile.body}"
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
