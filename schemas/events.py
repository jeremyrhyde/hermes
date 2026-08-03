"""Event schemas for the in-process pub/sub bus.

Events are how decoupled components talk: a producer publishes without caring
who listens; the WebSocket manager (and any future subscriber) registers
interest without caring what triggered the event.

The event types below are deliberately generic placeholders — replace them
with the real domain vocabulary once it exists. The wire shape (``type``,
``subject``, ``data``, ``timestamp``, ``source``) is what the web UI's
``applyEvent()`` switch is written against, so keep that stable.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EventType(str, Enum):
    """Discriminator for events on the bus.

    Values:
        SYSTEM_READY: Startup finished; the server is serving requests.
            Payload: ``{"detail": str}``.
        SYSTEM_ERROR: A component failed in a way the UI should surface.
            Payload: ``{"detail": str}``.
        STATE_CHANGED: Generic "something the UI renders has changed".
            Payload: ``{"state": <dict>}``, ``subject`` set to the entity id.
    """

    SYSTEM_READY = "system_ready"
    SYSTEM_ERROR = "system_error"
    STATE_CHANGED = "state_changed"


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
                    "type": "state_changed",
                    "subject": "widget-1",
                    "data": {"state": {"active": True}},
                    "timestamp": "2026-08-02T17:00:01Z",
                    "source": "api",
                },
            ]
        },
    )
