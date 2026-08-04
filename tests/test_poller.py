"""Poller: interval computation and backoff (research finding R2)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.events import EventBus
from core.state import StateStore
from schemas.source import SourceConfig
from services.pipeline import PollOutcome
from services.poller import Poller, backoff_seconds, compute_interval

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
CFG = SourceConfig(id="acx", type="stub", name="ACX", feed_url="https://x/feed")


def test_interval_takes_the_max_of_available_signals() -> None:
    assert compute_interval(
        cache_control_seconds=1800, ttl_seconds=3600,
        median_gap_seconds=None, min_s=900, max_s=14_400,
    ) == 3600


def test_interval_is_clamped_to_bounds() -> None:
    assert compute_interval(60, None, None, 900, 14_400) == 900
    assert compute_interval(999_999, None, None, 900, 14_400) == 14_400


def test_interval_falls_back_to_min_when_no_signals() -> None:
    assert compute_interval(None, None, None, 900, 14_400) == 900


def test_backoff_is_linear_and_capped() -> None:
    """R2: 1h x (error_count - 2), capped at 4h. The source project's
    'exponential' label is wrong; the formula is linear."""
    assert backoff_seconds(1) == 0
    assert backoff_seconds(2) == 0
    assert backoff_seconds(3) == 3600
    assert backoff_seconds(4) == 7200
    assert backoff_seconds(10) == 14_400


async def test_success_resets_error_count(store: StateStore) -> None:
    await store.upsert_source(CFG)
    await store.update_source_poll_state(
        "acx", etag=None, last_modified=None, content_hash=None,
        error_count=4, next_poll_at=NOW,
    )
    poller = Poller(store=store, pipeline=None, bus=EventBus(),
                    min_seconds=900, max_seconds=14_400)

    await poller.apply_outcome(CFG, PollOutcome(new_articles=1, ttl_seconds=3600), NOW)

    row = await store.get_source_row("acx")
    assert row["error_count"] == 0


async def test_failure_increments_and_backs_off(store: StateStore) -> None:
    await store.upsert_source(CFG)
    poller = Poller(store=store, pipeline=None, bus=EventBus(),
                    min_seconds=900, max_seconds=14_400)

    for _ in range(3):
        await poller.apply_failure(CFG, RuntimeError("boom"), NOW)

    row = await store.get_source_row("acx")
    assert row["error_count"] == 3
    # 3 failures -> 1h backoff
    assert row["next_poll_at"] == (NOW + timedelta(seconds=3600)).isoformat()


async def test_five_failures_disable_temporarily(store: StateStore) -> None:
    """R2: temporarily disabled after 5 consecutive failures, never permanently."""
    await store.upsert_source(CFG)
    poller = Poller(store=store, pipeline=None, bus=EventBus(),
                    min_seconds=900, max_seconds=14_400)

    for _ in range(5):
        await poller.apply_failure(CFG, RuntimeError("boom"), NOW)

    row = await store.get_source_row("acx")
    assert row["disabled_until"] is not None
    assert row["enabled"] == 1, "must never be permanently disabled"


async def test_is_running_is_false_before_start(store: StateStore) -> None:
    """main.py gates the manual-poll route on this."""
    poller = Poller(store=store, pipeline=None, bus=EventBus(),
                    min_seconds=900, max_seconds=14_400)
    assert poller.is_running is False


async def test_retry_after_does_not_count_as_failure(store: StateStore) -> None:
    """R2: 429/503 honor Retry-After without incrementing error_count."""
    await store.upsert_source(CFG)
    poller = Poller(store=store, pipeline=None, bus=EventBus(),
                    min_seconds=900, max_seconds=14_400)

    await poller.apply_outcome(
        CFG, PollOutcome(not_modified=True, retry_after_seconds=120), NOW
    )

    row = await store.get_source_row("acx")
    assert row["error_count"] == 0
    assert row["next_poll_at"] == (NOW + timedelta(seconds=120)).isoformat()
