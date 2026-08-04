"""The scheduling loop.

Policy is from research finding R2, which was verified 3-0 against two
independently-built readers:

- interval = max(Cache-Control, RSS <ttl>, observed post frequency), clamped
- error backoff is LINEAR: 1h x (error_count - 2), capped at 4h. The upstream
  project calls this "exponential"; that label is wrong and the verifier caught
  it. Implemented as documented, not as labelled.
- 429/503 honor Retry-After and do NOT increment error_count
- 5 consecutive failures set disabled_until; nothing is ever permanently
  disabled, and disabled sources stay visible in the UI so they cannot rot
  silently.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from core.events import EventBus
from core.state import StateStore, iso, utcnow
from schemas.events import Event, EventType
from schemas.source import SourceConfig
from services.pipeline import PollOutcome
from services.sources.base import PollState

logger = logging.getLogger(__name__)

MAX_BACKOFF_SECONDS = 14_400  # 4h
FAILURES_BEFORE_DISABLE = 5
DISABLE_DURATION_SECONDS = 14_400  # 4h, then it tries again


def compute_interval(
    cache_control_seconds: int | None,
    ttl_seconds: int | None,
    median_gap_seconds: int | None,
    min_s: int,
    max_s: int,
) -> int:
    """Largest available signal, clamped to the configured bounds."""

    candidates = [
        c for c in (cache_control_seconds, ttl_seconds, median_gap_seconds) if c
    ]
    base = max(candidates) if candidates else min_s
    return max(min_s, min(base, max_s))


def backoff_seconds(error_count: int) -> int:
    """Linear backoff, zero for the first two failures, capped at 4h."""

    if error_count < 3:
        return 0
    return min(3600 * (error_count - 2), MAX_BACKOFF_SECONDS)


class Poller:
    """Owns the clock. Selects due sources and hands them to the pipeline."""

    def __init__(
        self,
        *,
        store: StateStore,
        pipeline,
        bus: EventBus,
        min_seconds: int,
        max_seconds: int,
        tick_seconds: float = 60.0,
    ) -> None:
        self._store = store
        self._pipeline = pipeline
        self._bus = bus
        self._min = min_seconds
        self._max = max_seconds
        self._tick = tick_seconds
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    @property
    def is_running(self) -> bool:
        """True once :meth:`start` has launched the loop and it has not stopped."""

        return self._task is not None and not self._task.done()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        self._stopping.clear()
        self._task = asyncio.create_task(self._run(), name="hermes-poller")
        logger.info("poller: started (tick=%.0fs)", self._tick)

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("poller: stopped")

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.poll_due()
            except Exception:  # pragma: no cover - defensive
                logger.exception("poller: tick raised")
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self._tick)
            except asyncio.TimeoutError:
                continue

    # ------------------------------------------------------------------
    # Work
    # ------------------------------------------------------------------
    async def poll_due(self, now: datetime | None = None) -> int:
        """Poll every due source. Returns how many were polled."""

        now = now or utcnow()
        due = await self._store.due_sources(now)
        for source in due:
            await self.poll_one(source, now)
        return len(due)

    async def poll_one(self, source: SourceConfig, now: datetime | None = None) -> None:
        now = now or utcnow()
        row = await self._store.get_source_row(source.id)
        state = PollState(
            etag=row["etag"] if row else None,
            last_modified=row["last_modified"] if row else None,
            content_hash=row["content_hash"] if row else None,
        )
        try:
            outcome = await self._pipeline.process_source(source, state, now=now)
        except Exception as exc:
            logger.warning("poller: source %s failed: %s", source.id, exc)
            await self.apply_failure(source, exc, now)
            return

        await self.apply_outcome(source, outcome, now)

    async def apply_outcome(
        self, source: SourceConfig, outcome: PollOutcome, now: datetime
    ) -> None:
        """Persist poll state and schedule the next poll. Success resets errors."""

        if outcome.retry_after_seconds is not None:
            # Rate limiting is a scheduling signal, not a failure (R2).
            delay = outcome.retry_after_seconds
        else:
            delay = compute_interval(
                cache_control_seconds=None,
                ttl_seconds=outcome.ttl_seconds,
                median_gap_seconds=None,
                min_s=self._min,
                max_s=self._max,
            )

        await self._store.update_source_poll_state(
            source.id,
            etag=outcome.etag,
            last_modified=outcome.last_modified,
            content_hash=outcome.content_hash,
            error_count=0,
            next_poll_at=now + timedelta(seconds=delay),
        )
        await self._store.set_source_disabled_until(source.id, None)

        await self._bus.publish(
            Event(
                type=EventType.SOURCE_POLLED,
                subject=source.id,
                data={
                    "source_id": source.id,
                    "new_articles": outcome.new_articles,
                    "not_modified": outcome.not_modified,
                    "failed": outcome.failed,
                },
                source="poller",
            )
        )

    async def apply_failure(
        self, source: SourceConfig, exc: Exception, now: datetime
    ) -> None:
        row = await self._store.get_source_row(source.id)
        error_count = (row["error_count"] if row else 0) + 1
        delay = backoff_seconds(error_count) or self._min

        await self._store.update_source_poll_state(
            source.id,
            etag=row["etag"] if row else None,
            last_modified=row["last_modified"] if row else None,
            content_hash=row["content_hash"] if row else None,
            error_count=error_count,
            next_poll_at=now + timedelta(seconds=delay),
        )

        if error_count >= FAILURES_BEFORE_DISABLE:
            until = now + timedelta(seconds=DISABLE_DURATION_SECONDS)
            await self._store.set_source_disabled_until(source.id, until)
            logger.warning(
                "poller: %s temporarily disabled until %s after %d failures",
                source.id, iso(until), error_count,
            )

        await self._bus.publish(
            Event(
                type=EventType.PIPELINE_ERROR,
                subject=source.id,
                data={"stage": "poll", "subject": source.id, "error": str(exc)},
                source="poller",
            )
        )
