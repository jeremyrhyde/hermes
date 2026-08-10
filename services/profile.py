"""Profile distillation via the Anthropic Messages API.

Sibling of :mod:`services.scorer`, and deliberately the same shape: forced tool
use rather than prose parsing, and re-validation in Python of everything the
tool schema merely *asks* for — a JSON-schema ``required`` is a hint the API
does not enforce.

The distiller closes the loop the scorer opens. The scorer reads a profile and
judges articles by it; the distiller reads those judgements back — with the
reader's verdict on each — and proposes a revised profile. It returns a whole
body rather than a patch (spec §2.9): a patch format would need its own grammar
and its own failure mode when it fails to apply, to produce something the reader
reviews as prose anyway.

**What the model is shown is what the reader saw.** Headline, bullets,
categories, the score the current profile gave the article, and the rating. Not
the article text: the reader rated a card, so a signal derived from anything
they did not see would be attributing a judgement they never made.

**The proposal is never live.** Nothing here approves anything; the caller
stores the body as a pending version and the reader approves, edits, or rejects
it. That gate is also what enforces the prompt's instruction to preserve the
reader's stated criteria — a soft constraint by construction, which is precisely
why a human sits between the proposal and the scoring function.

Errors here carry no identifying context, which is the one visible difference in
shape from the scorer. The scorer names an article id because it runs per
article across a batch; a distillation is one call over the whole corpus, one at
a time, and there is no natural key to name.
"""

from __future__ import annotations

from typing import Any

# Versioned for the reason ``RUBRIC_VERSION`` is: a cosmetic edit to a judgment
# prompt materially changes its output, so an edit must surface as a git diff
# and force a bump rather than drifting silently between runs.
DISTILL_VERSION = "distill-v1"

DISTILL_PROMPT = """\
You maintain a reader's taste profile. The profile is the authority a separate \
scorer uses to judge articles from 0 to 100, so it is a working instrument, not \
an essay about the reader.

You are given the current profile and every article the reader has rated, with \
what they saw on the card, the score the current profile produced, and their \
verdict: +1 for a rating up, -1 for a rating down.

WHAT THE RATINGS MEAN.
A +1 on a low-scoring article and a -1 on a high-scoring one are the whole \
signal — they are where the current profile and the reader disagree. Agreements \
confirm what the profile already gets right; concentrate on the disagreements \
and on what distinguishes them from the agreements. Name the distinguishing \
property, not the topic: "treats a market through a supply constraint" is a \
property, "about markets" is a topic, and a profile written in topics scores \
every article on the same subject alike.

THE CATEGORIES.
The categories on each card are topical labels from an earlier stage. Read them \
to see what an article was about; do not carry them into the profile as \
vocabulary. A category that correlates with the ratings is usually standing in \
for a property you have not named yet — name the property.

WHAT TO PRESERVE.
The reader's stated criteria are theirs. Where the profile states what the \
reader values, keep that material intact and in their words. Revise the learned \
material around it — the observations, the examples, the emphases that were \
inferred rather than declared. If the ratings genuinely contradict a stated \
criterion, say so in the profile as an observation rather than deleting the \
criterion.

DISCIPLINE.
Do not rewrite for style. Change what the evidence supports changing and leave \
the rest, so that a diff against the current profile shows the reasoning rather \
than the churn. A handful of ratings is weak evidence: prefer sharpening an \
existing statement to adding a new claim, and do not invent a preference that \
two articles happen to share by coincidence.

OUTPUT.
Emit the complete revised profile as prose in the same register and format as \
the current one. It replaces the current body wholesale, so anything you omit \
is gone. Emit the profile only — no commentary, no summary of your changes, no \
preamble.
"""

# ``required`` is declared here *and* re-checked after the call, the lesson the
# scorer's bounds and phase 1's category ``enum`` both taught.
DISTILL_TOOL: dict[str, Any] = {
    "name": "emit_profile",
    "description": "Emit the complete revised taste profile.",
    "input_schema": {
        "type": "object",
        "properties": {
            "profile": {
                "type": "string",
                "description": (
                    "The full revised profile body, replacing the current one "
                    "wholesale. Prose, in the format of the current profile."
                ),
            },
        },
        "required": ["profile"],
    },
}


class DistillationError(RuntimeError):
    """Raised when no usable profile proposal could be produced."""


class ProfileDistiller:
    """Proposes a revised profile from the reader's ratings."""

    def __init__(self, client: Any, *, model: str, effort: str = "high") -> None:
        self._client = client
        self._model = model
        # Set explicitly rather than left to the API default, for the reason
        # the scorer sets it: effort changes what the model produces, and a
        # silent change to the default would move proposals with nothing to
        # explain why.
        self._effort = effort

    async def propose(self, current: str, rated: list[dict]) -> str:
        """Propose a replacement body for ``current`` from ``rated``.

        ``rated`` is the whole corpus, newest rating first, and is deliberately
        uncapped — spec §10.4, "the rated corpus is unbounded". Truncation, if
        it is ever needed, is the caller's decision and is safe in that order.
        """
        if not rated:
            # The caller's threshold should have prevented this. Reaching here
            # means the model would be asked to revise from no evidence, and a
            # revision from nothing is a rewrite.
            raise DistillationError("no rated articles to distill from")

        try:
            response = await self._client.messages.create(
                model=self._model,
                # Caps thinking *and* response together — adaptive thinking is
                # on by default on Claude 5 models. Four times the scorer's
                # budget: its response is one integer and a few sentences,
                # whereas a whole profile body is the response here, and
                # thinking at high effort has to work through every card in the
                # corpus before the body starts. Headroom is free — tokens are
                # billed as generated, not as reserved — and the corpus only
                # grows past the threshold that triggered the run.
                max_tokens=16384,
                # No `temperature`/`top_p`/`top_k`: Claude 5 models reject all
                # three outright with a 400. Effort is not a sampling knob and
                # travels in `output_config`.
                output_config={"effort": self._effort},
                system=DISTILL_PROMPT,
                tools=[DISTILL_TOOL],
                tool_choice={"type": "tool", "name": "emit_profile"},
                messages=[
                    {"role": "user", "content": self._build_context(current, rated)}
                ],
            )
        except Exception as exc:
            raise DistillationError(f"Anthropic API call failed: {exc}") from exc

        # Checked because a truncated profile is indistinguishable from a short
        # one. Running out of budget mid-JSON surfaces loudly, but running out
        # after the `profile` string has parsed and before the document is
        # finished yields a non-blank body that `_clean_profile` accepts and the
        # reader reviews as a proposal that stops mid-sentence.
        if getattr(response, "stop_reason", None) == "max_tokens":
            raise DistillationError(
                "the response hit max_tokens; the profile may be truncated"
            )

        payload = self._extract_tool_input(response)
        return self._clean_profile(payload.get("profile"))

    @classmethod
    def _build_context(cls, current: str, rated: list[dict]) -> str:
        """Assemble the current profile and the rated corpus.

        The profile comes first and the evidence second: the profile is what is
        being revised, and the ratings are read against it.
        """
        return (
            "CURRENT PROFILE.\n"
            f"{current}\n\n"
            f"RATED ARTICLES ({len(rated)}), newest rating first.\n\n"
            + "\n".join(cls._render(article) for article in rated)
        )

    @staticmethod
    def _render(article: dict) -> str:
        """Render one rated article as the reader saw it, plus their verdict."""
        # Never inferred from "not 1". A missing or unexpected rating rendered
        # as a down-vote would invert the reader's verdict — the one signal the
        # whole feature turns on — and nothing downstream could tell.
        rating = article.get("rating")
        if rating not in (1, -1):
            raise DistillationError(f"unexpected rating {rating!r}")
        verdict = "+1 (rated up)" if rating == 1 else "-1 (rated down)"

        # `rated_articles()` returns `score: None` for an article rated but
        # never scored. Formatted bare it would reach the prompt as the string
        # "None", which reads as a value rather than an absence.
        score = article.get("score")
        score_text = "not scored" if score is None else str(score)

        # Same absence-is-not-a-value rule as the score: a missing headline
        # would otherwise reach the prompt as the string "None".
        headline = str(article.get("headline") or "").strip() or "(no headline)"

        bullets = article.get("bullets") or []
        categories = article.get("categories") or []

        return (
            f"Rating: {verdict}\n"
            f"Score under the current profile: {score_text}\n"
            f"Headline: {headline}\n"
            "Bullets:\n"
            + "".join(f"- {bullet}\n" for bullet in bullets)
            + f"Categories: {', '.join(categories) or 'none'}\n"
        )

    @staticmethod
    def _clean_profile(raw: Any) -> str:
        """Require a non-blank body.

        Whitespace is absence: a blank body satisfies the schema's ``required``
        while leaving the scorer judging every article against nothing.
        """
        profile = str(raw or "").strip()
        if not profile:
            raise DistillationError("the proposed profile was empty")
        return profile

    @staticmethod
    def _extract_tool_input(response: Any) -> dict[str, Any]:
        for block in getattr(response, "content", []):
            if getattr(block, "type", None) == "tool_use":
                return dict(block.input)
        raise DistillationError("response contained no tool_use block")
