"""Substack RSS driver.

Implements the three-layer change detection confirmed by research finding R1:

1. HTTP conditional GET (``If-None-Match`` / ``If-Modified-Since``) -> 304
2. SHA-256 of the response body compared before parsing
3. Full parse only as fallback

Layer 1 only helps when the upstream honors it, which many feeds do not — hence
layer 2, which costs one hash over bytes we already have.

Substack RSS usually carries full post content in ``content:encoded``, so the
refetch path in ``services/extraction.py`` is often unnecessary. Whether it is
ever needed is spec open question 4, answered empirically in phase 1.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import feedparser
import httpx

from schemas.article import ArticleRef, DiscoverResult
from schemas.source import SourceConfig
from services.sources.base import PollState, SourceDriver

logger = logging.getLogger(__name__)

USER_AGENT = "Hermes/0.1 (personal feed reader; +https://github.com/)"


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _entry_content(entry: object) -> str | None:
    """Prefer full ``content:encoded`` over the truncated ``summary``."""

    content = getattr(entry, "content", None)
    if content:
        value = content[0].get("value")
        if value:
            return value
    return getattr(entry, "summary", None) or None


class SubstackDriver(SourceDriver):
    source_type = "substack"

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def discover(
        self, source: SourceConfig, state: PollState
    ) -> DiscoverResult:
        headers = {"User-Agent": USER_AGENT}
        if state.etag:
            headers["If-None-Match"] = state.etag
        if state.last_modified:
            headers["If-Modified-Since"] = state.last_modified

        response = await self._http.get(
            source.feed_url, headers=headers, follow_redirects=True
        )

        # --- Layer 1: conditional GET -----------------------------------
        if response.status_code == 304:
            logger.debug("substack: %s not modified (304)", source.id)
            return DiscoverResult(
                not_modified=True,
                etag=state.etag,
                last_modified=state.last_modified,
                content_hash=state.content_hash,
            )

        # Rate limiting is a scheduling signal, not a failure (research R2).
        if response.status_code in (429, 503):
            retry_after = response.headers.get("Retry-After")
            seconds = int(retry_after) if retry_after and retry_after.isdigit() else None
            logger.info(
                "substack: %s rate-limited (%d), retry_after=%s",
                source.id, response.status_code, seconds,
            )
            return DiscoverResult(
                not_modified=True,
                retry_after_seconds=seconds,
                etag=state.etag,
                last_modified=state.last_modified,
                content_hash=state.content_hash,
            )

        response.raise_for_status()

        body = response.content
        content_hash = hashlib.sha256(body).hexdigest()

        # --- Layer 2: content hash, before any XML parsing ---------------
        if state.content_hash and content_hash == state.content_hash:
            logger.debug("substack: %s unchanged by hash", source.id)
            return DiscoverResult(
                not_modified=True,
                etag=response.headers.get("ETag") or state.etag,
                last_modified=response.headers.get("Last-Modified")
                or state.last_modified,
                content_hash=content_hash,
            )

        # --- Layer 3: parse ----------------------------------------------
        parsed = feedparser.parse(body)

        refs = [
            ArticleRef(
                source_id=source.id,
                guid=getattr(entry, "id", None) or entry.link,
                url=entry.link,
                title=getattr(entry, "title", "(untitled)"),
                author=getattr(entry, "author", None),
                published_at=_parse_date(getattr(entry, "published", None)),
                summary_html=_entry_content(entry),
            )
            for entry in parsed.entries
            if getattr(entry, "link", None)
        ]

        # Keyed on ``refs``, not ``parsed.entries``: feedparser is lenient and
        # almost always salvages a stub entry from a mid-document truncation —
        # the failure a flaky CDN actually produces. Those stubs carry no link
        # and are dropped above, so ``parsed.entries`` is non-empty while the
        # poll yielded nothing. Without this signal a broken feed is
        # indistinguishable from an author who simply did not publish.
        if parsed.bozo and not refs:
            logger.warning(
                "substack: %s returned unparseable feed: %r",
                source.id, getattr(parsed, "bozo_exception", None),
            )

        ttl = getattr(parsed.feed, "ttl", None) if hasattr(parsed, "feed") else None
        ttl_seconds = int(ttl) * 60 if ttl and str(ttl).isdigit() else None

        return DiscoverResult(
            not_modified=False,
            refs=refs,
            etag=response.headers.get("ETag"),
            last_modified=response.headers.get("Last-Modified"),
            content_hash=content_hash,
            ttl_seconds=ttl_seconds,
        )

    async def fetch_html(self, ref: ArticleRef) -> str:
        response = await self._http.get(
            ref.url,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )
        response.raise_for_status()
        return response.text
