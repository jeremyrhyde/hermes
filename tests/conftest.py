"""Shared fixtures. Everything here is hermetic — no network, no real API keys."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.state import StateStore
from schemas.source import SourceConfig

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
async def store(tmp_path) -> StateStore:
    """A started StateStore on a temp DB. Closed on teardown."""
    s = StateStore(str(tmp_path / "test.db"))
    await s.start()
    try:
        yield s
    finally:
        await s.close()


@pytest.fixture
def source_config() -> SourceConfig:
    return SourceConfig(
        id="acx",
        type="substack",
        name="Astral Codex Ten",
        feed_url="https://astralcodexten.substack.com/feed",
    )


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 8, 2, 12, 0, 0, tzinfo=timezone.utc)


def read_fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def make_article(
    *,
    article_id: int = 1,
    title: str = "The First Post",
    text: str | None = "Body text.",
    author: str | None = "Scott Alexander",
) -> "Article":
    """Build an Article for tests without touching the DB."""
    from schemas.article import Article

    return Article(
        id=article_id,
        source_id="acx",
        guid=f"g{article_id}",
        canonical_url=f"https://acx.substack.com/p/{article_id}",
        title=title,
        author=author,
        published_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
        text=text,
        word_count=len((text or "").split()),
    )
