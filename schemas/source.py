"""Source configuration schemas.

``SourceConfig`` has a typed core plus an open-ended ``params`` dict so a new
source type adds keys to ``sources.yaml`` rather than fields to this model
(spec section 12.6). ``SourceRef`` is the denormalized identity that ships in
the feed card (spec section 12.5).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SourceRef(BaseModel):
    """Source identity as rendered on a feed card."""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    type: str
    # Room to grow: icon_url, colour, favicon — added without touching Article.
    icon_url: str | None = None


class SourceConfig(BaseModel):
    """One entry in ``sources.yaml``."""

    model_config = ConfigDict(extra="ignore")

    id: str
    type: str
    name: str
    feed_url: str
    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)

    def to_ref(self) -> SourceRef:
        return SourceRef(id=self.id, name=self.name, type=self.type)


class CategoriesConfig(BaseModel):
    """Topic category configuration (spec section 3).

    ``vocabulary`` is what Claude may assign during summarization;
    ``filters`` is the subset the feed UI offers as filter buttons.
    Both default empty so an unconfigured ``sources.yaml`` reproduces
    current behaviour exactly: no tagging, no filter row. Entries are
    normalized (lowercased, de-duplicated, order preserved) by
    ``load_sources_config`` — not here — so there is exactly one place
    that does it.
    """

    model_config = ConfigDict(extra="ignore")

    vocabulary: list[str] = Field(default_factory=list)
    filters: list[str] = Field(default_factory=list)


class SourcesConfig(BaseModel):
    """Top level of ``sources.yaml``."""

    model_config = ConfigDict(extra="ignore")

    sources: list[SourceConfig] = Field(default_factory=list)
    categories: CategoriesConfig = Field(default_factory=CategoriesConfig)
