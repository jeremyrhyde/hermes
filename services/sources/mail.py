"""EmailDriver — newsletters read from a mailbox label over IMAP.

For publications whose RSS Hermes cannot fetch (``*.substack.com`` answers
Hermes's HTTP client with a Cloudflare challenge), subscribe by email instead:
a mail filter puts the newsletters under one label (``IMAP_LABEL``), and each
source here picks out its publication by ``List-ID``. Substack emails carry the
full post, so the email body is the article text — the pipeline never needs
the web page (``fetch_html`` returns nothing; a teaser-only email becomes
unusable like any other thin article).

Only *posts* become articles: an email counts as one when it links to a post
(``…/p/<slug>``) of its own publication. Welcome emails, sign-in codes and
receipts have no such link and are skipped. The post's canonical URL —
``<List-URL>/p/<slug>``, the same shape RSS guids take — is both the article's
URL and its guid.

The mailbox is only ever read: the label is opened read-only (EXAMINE) and
messages are fetched with BODY.PEEK, so nothing is marked as read.

``sources.yaml`` entry::

    - id: epoch-ai
      type: email
      name: Epoch AI
      feed_url: https://epochai.substack.com     # the publication's home page
      params:
        list_id: epochai.substack.com            # the List-ID header, without <>
"""

from __future__ import annotations

import asyncio
import email
import email.policy
import hashlib
import imaplib
import logging
import re
from collections import Counter
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import unquote, urlsplit

from schemas.article import ArticleRef, DiscoverResult
from schemas.source import SourceConfig
from services.sources.base import PollState, SourceDriver

logger = logging.getLogger(__name__)

# Defence in depth: account mail that could conceivably link a post.
_NOT_A_POST = re.compile(
    r"verification code|^welcome to|confirm your|your (?:subscription|receipt|payment)|sign in to|password",
    re.IGNORECASE,
)
# A post link: …/p/<slug> on the publication's host, or open.substack.com/pub/<pub>/p/<slug>.
_POST_PATH = re.compile(r"^/p/([a-z0-9][a-z0-9-]*)/?$", re.IGNORECASE)
_OPEN_PATH = re.compile(r"^/pub/([a-z0-9-]+)/p/([a-z0-9][a-z0-9-]*)/?$", re.IGNORECASE)


class Mailbox(Protocol):
    """Raw messages under one label, newest last, read without marking them read."""

    def messages(self, since: date | None) -> tuple[str, list[bytes]]:
        """Return a fingerprint of the label's matching contents and the raw messages."""


class ImapMailbox:
    """The real mailbox: one IMAP session per call, read-only."""

    def __init__(self, host: str, user: str, password: str, label: str, timeout: float = 30.0) -> None:
        self._host, self._user, self._label, self._timeout = host, user, label, timeout
        self._password = password.replace(" ", "")  # app passwords are shown in groups of four

    def messages(self, since: date | None) -> tuple[str, list[bytes]]:
        imap = imaplib.IMAP4_SSL(self._host, timeout=self._timeout)
        try:
            imap.login(self._user, self._password)
            status, data = imap.select(f'"{self._label}"', readonly=True)
            if status != "OK":
                raise RuntimeError(f"mail label {self._label!r} not found")
            validity = imap.response("UIDVALIDITY")[1][0] or b""
            criteria = ["SINCE", since.strftime("%d-%b-%Y")] if since else ["ALL"]
            _, found = imap.uid("SEARCH", *criteria)
            uids = found[0].split() if found and found[0] else []
            raw: list[bytes] = []
            for uid in uids:
                _, parts = imap.uid("FETCH", uid, "(BODY.PEEK[])")
                raw.extend(p[1] for p in parts if isinstance(p, tuple))
            fingerprint = hashlib.sha256(validity + b":" + b",".join(uids)).hexdigest()
            return fingerprint, raw
        finally:
            try:
                imap.logout()
            except Exception:  # noqa: BLE001 — a failed logout must not mask the real outcome
                pass


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.hrefs.append(href)


def _list_id(msg: EmailMessage) -> str | None:
    value = msg.get("List-ID")
    if not value:
        return None
    match = re.search(r"<([^>]+)>", str(value))
    return (match.group(1) if match else str(value)).strip().lower()


def _list_url(msg: EmailMessage) -> str | None:
    value = msg.get("List-URL")
    if not value:
        return None
    match = re.search(r"<([^>]+)>", str(value))
    return (match.group(1) if match else str(value)).strip().rstrip("/")


def post_url(msg: EmailMessage, html: str) -> str | None:
    """The canonical URL of the post an email carries, or None if it isn't a post.

    The slug is the one the email links to most (title, share, like and comment
    links all point at the post; a footer's other posts appear once).
    """

    home = _list_url(msg)
    pub = (_list_id(msg) or "").split(".")[0]
    if not home or not pub:
        return None
    home_host = urlsplit(home).netloc.lower()
    parser = _Links()
    parser.feed(html)
    slugs: Counter[str] = Counter()
    for href in parser.hrefs:
        parts = urlsplit(unquote(href))
        host = parts.netloc.lower()
        if host in (home_host, f"{pub}.substack.com"):
            m = _POST_PATH.match(parts.path)
            if m:
                slugs[m.group(1).lower()] += 1
        elif host == "open.substack.com":
            m = _OPEN_PATH.match(parts.path)
            if m and m.group(1).lower() == pub:
                slugs[m.group(2).lower()] += 1
    if not slugs:
        return None
    return f"{home}/p/{slugs.most_common(1)[0][0]}"


def _author(from_header: str) -> str | None:
    name, _ = parseaddr(from_header)
    name = name.strip().strip('"')
    # "Dave Friedman from Buy the Rumor, Sell the News" → "Dave Friedman"
    return name.split(" from ")[0].strip() or None


def parse_post(raw: bytes) -> tuple[str | None, ArticleRef | None]:
    """(list_id, ref) for one raw email; ref is None when it isn't a post."""

    msg = email.message_from_bytes(raw, policy=email.policy.default)
    assert isinstance(msg, EmailMessage)
    list_id = _list_id(msg)
    subject = str(msg.get("Subject", "")).strip()
    if not list_id or _NOT_A_POST.search(subject):
        return list_id, None
    part = msg.get_body(preferencelist=("html", "plain"))
    body = part.get_content() if part is not None else ""
    if part is not None and part.get_content_subtype() == "plain":
        body = "".join(f"<p>{line}</p>" for line in body.splitlines() if line.strip())
    url = post_url(msg, body)
    if url is None:
        return list_id, None
    try:
        published = parsedate_to_datetime(str(msg["Date"])) if msg.get("Date") else None
    except (TypeError, ValueError):
        published = None
    return list_id, ArticleRef(
        source_id="",  # set per source by the driver
        guid=url,
        url=url,
        title=subject or "(untitled)",
        author=_author(str(msg.get("From", ""))),
        published_at=published,
        summary_html=body,
    )


class EmailDriver(SourceDriver):
    source_type = "email"

    def __init__(
        self,
        mailbox: Callable[[], Mailbox] | None,
        lookback_days: int = 7,
        today: Callable[[], date] = lambda: datetime.now(timezone.utc).date(),
    ) -> None:
        """``mailbox`` builds a Mailbox per poll; None when IMAP isn't configured."""

        self._mailbox = mailbox
        self._lookback_days = lookback_days
        self._today = today

    async def discover(self, source: SourceConfig, state: PollState) -> DiscoverResult:
        if self._mailbox is None:
            raise RuntimeError("email source needs IMAP_USER and IMAP_PASSWORD in .env")
        list_id = str(source.params.get("list_id", "")).strip().lower()
        if not list_id:
            raise ValueError(f"email source {source.id!r} needs params.list_id")
        since = self._today() - timedelta(days=self._lookback_days) if self._lookback_days else None
        mailbox = self._mailbox()
        fingerprint, raw = await asyncio.to_thread(mailbox.messages, since)
        content_hash = hashlib.sha256(f"{list_id}:{fingerprint}".encode()).hexdigest()
        if state.content_hash == content_hash:
            return DiscoverResult(not_modified=True, content_hash=content_hash)

        refs: dict[str, ArticleRef] = {}
        skipped = 0
        for message in raw:
            msg_list, ref = parse_post(message)
            if msg_list != list_id:
                continue
            if ref is None:
                skipped += 1
                continue
            refs.setdefault(ref.guid, ref.model_copy(update={"source_id": source.id}))
        if skipped:
            logger.debug("email: %s skipped %d non-post message(s)", source.id, skipped)
        return DiscoverResult(not_modified=False, refs=list(refs.values()), content_hash=content_hash)

    async def fetch_html(self, ref: ArticleRef) -> str:
        # The email is the article; the web page is what we can't fetch. Nothing
        # here, so a thin (teaser-only) email is marked unusable, not retried.
        return ""
