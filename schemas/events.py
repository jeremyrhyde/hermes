"""Event schemas for the in-process pub/sub bus.

Events are how decoupled components talk: a producer publishes without caring
who listens; the WebSocket manager (and any future subscriber) registers
interest without caring what triggered the event.

The event types below are the feed-pipeline vocabulary: polling, ingestion,
summarization, scoring, profile distillation, and errors. The wire shape
(``type``, ``subject``, ``data``, ``timestamp``, ``source``) is what the web
UI's ``applyEvent()`` switch is written against, so keep that stable.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EventType(str, Enum):
    """Discriminator for events on the bus.

    Values:
        SYSTEM_READY: Startup finished. Payload ``{"detail": str}``.
        SOURCE_POLLED: One source finished a poll cycle.
            Payload ``{"source_id": str, "new_articles": int,
                       "not_modified": bool}``.
        ARTICLE_INGESTED: A new article row was created.
            Payload ``{"article_id": int, "title": str, "source_id": str}``.
        ARTICLE_SUMMARIZED: A summary landed; the UI can render the card.
            Payload ``{"item": <FeedItem dict>}``.
        ARTICLE_SCORED: A score landed. Phase 3.
            Payload ``{"article_id": int, "score": int}``.
        PROFILE_PROPOSED: A distillation awaits review. Phase 4.
            Payload ``{"version": str}``.
        PIPELINE_ERROR: A stage failed for one article or source.
            Payload ``{"stage": str, "subject": str, "error": str}``.
    """

    SYSTEM_READY = "system_ready"
    SOURCE_POLLED = "source_polled"
    ARTICLE_INGESTED = "article_ingested"
    ARTICLE_SUMMARIZED = "article_summarized"
    ARTICLE_SCORED = "article_scored"
    PROFILE_PROPOSED = "profile_proposed"
    PIPELINE_ERROR = "pipeline_error"


class Event(BaseModel):
    """An event published on the bus.

    Fields:
        type: Discriminator, see :class:`EventType`.
        subject: ID of the entity the event refers to, if any. Optional so
            system-wide events don't have to invent one.
        data: Payload dict; see :class:`EventType` for per-type contents.
        timestamp: When the event was created. Defaults to the current UTC
            time as a timezone-aware ``datetime`` at instantiation.
        source: Free-form string identifying the trigger origin
            (e.g. ``"api"``, ``"startup"``, ``"scheduler"``). Used for
            diagnostics and to disambiguate cascading effects.
    """

    type: EventType
    subject: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    source: str

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "type": "system_ready",
                    "subject": None,
                    "data": {"detail": "hermes 0.1.0"},
                    "timestamp": "2026-08-02T17:00:00Z",
                    "source": "startup",
                },
                {
                    "type": "article_summarized",
                    "subject": "42",
                    "data": {"item": {"article_id": 42, "headline": "..."}},
                    "timestamp": "2026-08-02T17:00:00Z",
                    "source": "pipeline",
                },
            ]
        },
    )
