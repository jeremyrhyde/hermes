"""Article, summary, score, and feed-card schemas.

Every model sets ``extra="ignore"`` and defaults optional fields, so adding a
field never breaks a stored row or an in-flight payload (spec section 12.1).
Each carries a ``metadata`` dict for additive, non-queried data (spec 12.2).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from schemas.source import SourceRef


class ArticleRef(BaseModel):
    """A discovered article, before fetch. What a feed entry gives us."""

    model_config = ConfigDict(extra="ignore")

    source_id: str
    guid: str
    url: str
    title: str
    author: str | None = None
    published_at: datetime | None = None
    # Feed-provided content, when the feed carries full text.
    summary_html: str | None = None


class Article(BaseModel):
    """A stored article with its stage checkpoints."""

    model_config = ConfigDict(extra="ignore")

    id: int
    source_id: str
    guid: str
    canonical_url: str
    title: str
    author: str | None = None
    published_at: datetime | None = None
    fetched_at: datetime

    raw_html: str | None = None
    text: str | None = None
    word_count: int | None = None

    extracted_at: datetime | None = None
    summarized_at: datetime | None = None
    scored_at: datetime | None = None

    last_error: str | None = None
    error_stage: str | None = None

    metadata: dict[str, Any] = Field(default_factory=dict)


class Summary(BaseModel):
    """Exactly five bullets. Enforced here and at the tool-schema boundary."""

    model_config = ConfigDict(extra="ignore")

    headline: str
    bullets: list[str] = Field(min_length=5, max_length=5)
    model: str
    prompt_version: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class Score(BaseModel):
    """0-100 with the inputs that produced it (spec 12.4). Unused until phase 3."""

    model_config = ConfigDict(extra="ignore")

    score: int = Field(ge=0, le=100)
    rationale: str | None = None
    rubric_version: str
    profile_version: str
    signals: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DiscoverResult(BaseModel):
    """Outcome of one poll of one source.

    ``not_modified=True`` is distinct from ``refs=[]``: the first means the feed
    did not change, the second means it changed to empty. They imply different
    next-poll behavior.
    """

    model_config = ConfigDict(extra="ignore")

    not_modified: bool = False
    refs: list[ArticleRef] = Field(default_factory=list)
    etag: str | None = None
    last_modified: str | None = None
    content_hash: str | None = None
    retry_after_seconds: int | None = None
    ttl_seconds: int | None = None


class FeedItem(BaseModel):
    """What the UI list renders. Assembled by the API, never a serialized row."""

    model_config = ConfigDict(extra="ignore")

    article_id: int
    headline: str
    bullets: list[str]
    url: str
    published_at: datetime | None = None
    source: SourceRef
    score: int | None = None
    rating: int | None = None
    badges: list[str] = Field(default_factory=list)
