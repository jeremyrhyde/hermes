"""Full-text extraction.

Per research finding R4 the working pattern is: refetch the original URL, run a
Readability-class extractor, and keep per-site selector overrides plus a
headless-browser path as escape hatches. ``trafilatura`` is the Python analogue
of the Readability step.

Phase 1 ships only the extractor. Substack RSS usually carries full content in
``content:encoded``, so the pipeline prefers that and only refetches when the
feed body is missing or too short — the escape hatches are added in phase 2 if
phase 1 shows they are needed (spec open question 4).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import trafilatura

logger = logging.getLogger(__name__)

#: Below this, feed-provided content is treated as truncated and we refetch.
#: Below it *again* after the refetch, the article is unusable and is never
#: summarized. Sits between the observed email-teaser cluster (81-102 words)
#: and the shortest real article in the corpus (362) — one corpus, not a
#: validated threshold.
MIN_USABLE_WORDS = 120

#: Substrings that identify a subscriber wall. This list can only ever *label*
#: an article that word count has already rejected — it never rejects one on
#: its own. That constraint is what keeps it from drifting into a content
#: filter, which is deliberately not part of this system: taste belongs to the
#: scorer, not to a hardcoded string list.
PAYWALL_MARKERS = (
    "this post is for paid subscribers",
    "this post is for paying subscribers",
    "subscribe to continue reading",
    "this post is for subscribers",
)


def unusable_reason(result: "ExtractionResult") -> str:
    """Label an extraction already rejected on word count.

    ``paywalled`` and ``thin after refetch (N words)`` read differently on the
    health panel, and the difference is what tells you whether to change a
    source or fix a bug.
    """

    text = result.text.strip().lower()
    if any(marker in text for marker in PAYWALL_MARKERS):
        return "paywalled"
    return f"thin after refetch ({result.word_count} words)"


@dataclass(frozen=True)
class ExtractionResult:
    text: str
    word_count: int


def extract_text(html: str) -> ExtractionResult:
    """Extract readable body text from *html*.

    Never raises — an unextractable page yields an empty result so the caller
    can record a stage error and move on rather than aborting the batch.
    """

    # This guard is deliberately total: any non-string, None, empty, or
    # whitespace-only input degrades to an empty result rather than raising,
    # because the caller processes a batch and one bad article must not abort
    # it. isinstance is checked explicitly rather than relying on the str type
    # hint, since a malformed content:encoded value or a misbound parsed-XML
    # element can hand this a non-string at runtime.
    if not isinstance(html, str) or not html.strip():
        return ExtractionResult(text="", word_count=0)

    try:
        extracted = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
        )
    except Exception:  # pragma: no cover - defensive
        logger.exception("extraction: trafilatura raised")
        extracted = None

    text = (extracted or "").strip()
    return ExtractionResult(text=text, word_count=len(text.split()))
