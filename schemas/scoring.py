"""The score a single article earned against the reader's taste profile.

Separate from :mod:`schemas.article` because a score is not a property of an
article the way its text is — it is the output of a *function* (rubric ×
profile) applied to one, and that function changes underneath a fixed corpus.
Hence both versions on every row: a score is only comparable to another score
produced by the same pair.

``value`` rather than ``score`` so ``score.value`` reads as the number and
``Score`` reads as the record, which is what callers actually pass around.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Score(BaseModel):
    """A 0-100 rating with the evidence and the versions that produced it."""

    model_config = ConfigDict(extra="ignore")

    value: int = Field(ge=0, le=100)
    # Required, not optional: without a rationale a wrong score is not
    # diagnosable, and diagnosing wrong scores is the whole of phase 3.
    rationale: str
    rubric_version: str
    profile_version: str
    # What the model was shown, not what it concluded. Recorded so "were the
    # summaries the bottleneck?" is answerable from the data later rather than
    # from memory.
    signals: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
