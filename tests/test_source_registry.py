"""SourceRegistry: driver lookup by source type."""

from __future__ import annotations

import pytest

from schemas.article import DiscoverResult
from schemas.source import SourceConfig
from services.sources.base import SourceDriver
from services.sources.registry import SourceRegistry


class FakeDriver(SourceDriver):
    source_type = "fake"

    async def discover(self, source, state):
        return DiscoverResult(not_modified=True)

    async def fetch_html(self, ref):
        return "<html></html>"


def test_registry_resolves_by_type() -> None:
    registry = SourceRegistry()
    driver = FakeDriver()
    registry.register(driver)

    cfg = SourceConfig(id="x", type="fake", name="X", feed_url="https://x/feed")
    assert registry.for_source(cfg) is driver


def test_unknown_type_raises_keyerror() -> None:
    registry = SourceRegistry()
    cfg = SourceConfig(id="x", type="nope", name="X", feed_url="https://x/feed")

    with pytest.raises(KeyError, match="nope"):
        registry.for_source(cfg)


def test_known_types_is_introspectable() -> None:
    registry = SourceRegistry()
    registry.register(FakeDriver())
    assert registry.known_types() == ["fake"]
