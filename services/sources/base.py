"""The SourceDriver contract.

A driver knows two things: how to list what is new in a source, and how to
retrieve one article's HTML. It knows nothing about filtering, summarization,
storage, or scheduling — those belong to the pipeline and poller.

Adding a source type is one new module plus one ``registry.register(...)`` call.
It never requires a change to the pipeline.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from schemas.article import ArticleRef, DiscoverResult
from schemas.source import SourceConfig


@dataclass(frozen=True)
class PollState:
    """Conditional-GET state carried into a discover call (research finding R1)."""

    etag: str | None = None
    last_modified: str | None = None
    content_hash: str | None = None


class SourceDriver(ABC):
    """One per source type."""

    #: Matches ``SourceConfig.type``. Used by the registry for lookup.
    source_type: str

    @abstractmethod
    async def discover(
        self, source: SourceConfig, state: PollState
    ) -> DiscoverResult:
        """Return new article refs, or ``not_modified``.

        Implementations are responsible for conditional GET and for the content
        hash short-circuit. They must not raise on an unchanged feed — that is
        the ``not_modified=True`` path, not an error.
        """

    @abstractmethod
    async def fetch_html(self, ref: ArticleRef) -> str:
        """Retrieve the raw HTML for one article. Raises on transport failure."""
