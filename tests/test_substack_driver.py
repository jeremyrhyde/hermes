"""SubstackDriver: three-layer change detection (research finding R1)."""

from __future__ import annotations

import hashlib
import logging

import httpx
import pytest

from schemas.source import SourceConfig
from services.sources.base import PollState
from services.sources.substack import SubstackDriver
from tests.conftest import read_fixture

FEED = read_fixture("substack_basic.xml")
CFG = SourceConfig(
    id="acx", type="substack", name="ACX",
    feed_url="https://astralcodexten.substack.com/feed",
)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_parses_entries_into_refs() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=FEED, headers={"ETag": 'W/"v1"'})

    async with _client(handler) as http:
        result = await SubstackDriver(http).discover(CFG, PollState())

    assert result.not_modified is False
    assert len(result.refs) == 2
    assert result.refs[0].title == "The First Post"
    assert result.refs[0].author == "Scott Alexander"
    assert result.refs[0].published_at is not None
    assert result.etag == 'W/"v1"'
    assert result.ttl_seconds == 3600


async def test_layer_one_conditional_get_sends_headers_and_honors_304() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(304)

    state = PollState(
        etag='W/"v1"',
        last_modified="Sat, 02 Aug 2026 10:00:00 GMT",
        content_hash="abc123",
    )
    async with _client(handler) as http:
        result = await SubstackDriver(http).discover(CFG, state)

    assert seen["if-none-match"] == 'W/"v1"'
    assert seen["if-modified-since"] == "Sat, 02 Aug 2026 10:00:00 GMT"
    assert seen["user-agent"].startswith("Hermes/")
    assert result.not_modified is True
    assert result.refs == []

    # The 304 path must echo state back, not null it out. If the poller
    # persists nulls here, conditional GET stops working on the next poll and
    # every feed refetches in full forever.
    assert result.etag == 'W/"v1"'
    assert result.last_modified == "Sat, 02 Aug 2026 10:00:00 GMT"
    assert result.content_hash == "abc123"


async def test_layer_two_content_hash_short_circuits_before_parse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 200 whose body hash is unchanged must not be parsed.

    Asserting only ``not_modified``/``refs`` would pass just as well for an
    implementation that parses first and checks the hash afterward — which
    forfeits the entire point of layer 2. So we poison the parser: if
    ``discover`` reaches it, the call raises instead of returning.
    """
    known = hashlib.sha256(FEED).hexdigest()

    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("feedparser.parse was called before the hash check")

    monkeypatch.setattr("services.sources.substack.feedparser.parse", boom)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=FEED)

    async with _client(handler) as http:
        result = await SubstackDriver(http).discover(
            CFG, PollState(content_hash=known)
        )

        assert result.not_modified is True
        assert result.refs == []

        # Control: with a mismatched hash the poisoned parser MUST be reached.
        # Without this, the assertions above could pass for an unrelated
        # reason — e.g. a discover() that returned early and never parsed at
        # all — and the test would no longer prove anything about ordering.
        with pytest.raises(AssertionError, match="before the hash check"):
            await SubstackDriver(http).discover(
                CFG, PollState(content_hash="a-different-hash")
            )


async def test_malformed_xml_yields_no_refs_without_raising(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A broken feed is a bad poll, not a crash — the poller handles backoff.

    It must still be visible to an operator. feedparser salvages a linkless
    stub entry from this truncated fixture, so the warning has to key on the
    refs we actually produced rather than on ``parsed.entries``.
    """
    broken = read_fixture("substack_malformed.xml")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=broken)

    with caplog.at_level(logging.WARNING, logger="services.sources.substack"):
        async with _client(handler) as http:
            result = await SubstackDriver(http).discover(CFG, PollState())

    assert result.refs == []
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "a truncated feed must leave an operator signal"
    assert any(CFG.id in r.getMessage() for r in warnings)


@pytest.mark.parametrize("status", [429, 503])
async def test_rate_limit_surfaces_retry_after(status: int) -> None:
    """Rate limiting is a scheduling signal, not a failure.

    Raising here would make the poller count a failure and back off, which is
    the opposite of honoring Retry-After.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers={"Retry-After": "120"})

    async with _client(handler) as http:
        result = await SubstackDriver(http).discover(CFG, PollState())

    assert result.retry_after_seconds == 120
    assert result.not_modified is True


async def test_server_error_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    async with _client(handler) as http:
        with pytest.raises(httpx.HTTPStatusError):
            await SubstackDriver(http).discover(CFG, PollState())


async def test_fetch_html_returns_body() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, text="<html><body>hi</body></html>")

    from schemas.article import ArticleRef

    ref = ArticleRef(source_id="acx", guid="g", url="https://x/p/1", title="T")
    async with _client(handler) as http:
        html = await SubstackDriver(http).fetch_html(ref)

    assert "hi" in html
    # Separate code path from discover(); it can lose the header independently.
    assert seen["user-agent"].startswith("Hermes/")
