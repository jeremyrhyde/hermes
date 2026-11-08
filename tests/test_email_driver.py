"""EmailDriver: newsletters read from a mail label (fake mailbox, no network)."""

from __future__ import annotations

import email
import email.policy
from datetime import date, datetime, timezone
from email.message import EmailMessage

import pytest

from schemas.source import SourceConfig
from services.sources.base import PollState
from services.sources.mail import EmailDriver, parse_post, post_url

EPOCH = SourceConfig(
    id="epoch-ai", type="email", name="Epoch AI",
    feed_url="https://epochai.substack.com", params={"list_id": "epochai.substack.com"},
)


def message(
    subject: str = "GLM's patch gap",
    list_id: str = "epochai.substack.com",
    list_url: str = "https://epochai.substack.com/",
    sender: str = "Epoch AI <epochai@substack.com>",
    html: str | None = None,
    when: str = "Sat, 07 Nov 2026 13:00:00 +0000",
) -> bytes:
    if html is None:
        words = " ".join(["word"] * 400)
        html = f"""<html><body>
          <a href="https://substack.com/app-link/post?publication_id=1&post_id=2">{subject}</a>
          <a href="https://epochai.substack.com/p/glm-patch-gap?utm_source=substack&utm_medium=email">Share</a>
          <a href="https://open.substack.com/pub/epochai/p/glm-patch-gap?r=x&utm_medium=email">Comment</a>
          <a href="https://email.mg-d0.substack.com/c/eJx0kcuu2yAY">Like</a>
          <p>{words}</p>
          <a href="https://epochai.substack.com/p/an-older-post">Read our previous post</a>
        </body></html>"""
    m = EmailMessage()
    m["From"] = sender
    m["Subject"] = subject
    m["Date"] = when
    m["List-ID"] = f"<{list_id}>"
    m["List-URL"] = f"<{list_url}>"
    m["Message-ID"] = f"<{abs(hash((subject, list_id)))}@substack.com>"
    m.set_content("plain version")
    m.add_alternative(html, subtype="html")
    return bytes(m)


class FakeMailbox:
    def __init__(self, raw: list[bytes], fingerprint: str = "v1") -> None:
        self.raw, self.fingerprint, self.since = raw, fingerprint, None

    def messages(self, since):
        self.since = since
        return self.fingerprint, self.raw


def driver(box: FakeMailbox, lookback: int = 7) -> EmailDriver:
    return EmailDriver(lambda: box, lookback_days=lookback, today=lambda: date(2026, 11, 8))


def test_a_post_becomes_an_article_with_its_canonical_url():
    list_id, ref = parse_post(message())
    assert list_id == "epochai.substack.com"
    assert ref.url == ref.guid == "https://epochai.substack.com/p/glm-patch-gap"  # the most-linked slug
    assert ref.title == "GLM's patch gap"
    assert ref.author == "Epoch AI"
    assert ref.published_at == datetime(2026, 11, 7, 13, 0, tzinfo=timezone.utc)
    assert "word word" in ref.summary_html


def test_custom_domain_publication_uses_its_own_host():
    html = '<a href="https://www.understandingrobots.org/p/hands?utm_source=email">x</a><p>text</p>'
    _, ref = parse_post(message(
        list_id="understandingrobots.substack.com", list_url="https://www.understandingrobots.org/",
        sender="Kai Williams from Understanding Robots <understandingrobots@substack.com>", html=html,
    ))
    assert ref.url == "https://www.understandingrobots.org/p/hands"
    assert ref.author == "Kai Williams"


@pytest.mark.parametrize("subject", ["814180 is your Substack verification code", "Welcome to Epoch AI’s Substack"])
def test_account_mail_is_not_a_post(subject):
    assert parse_post(message(subject=subject))[1] is None


def test_mail_without_a_post_link_is_not_a_post():
    html = '<a href="https://email.mg-d0.substack.com/c/abc">Read</a><p>Thanks for subscribing</p>'
    assert parse_post(message(subject="Thanks!", html=html))[1] is None


def test_links_to_another_publication_do_not_count():
    html = '<a href="https://other.substack.com/p/theirs">x</a><a href="https://open.substack.com/pub/other/p/theirs">y</a>'
    m = email.message_from_bytes(message(html=html), policy=email.policy.default)
    assert post_url(m, html) is None


async def test_discover_picks_this_publication_and_dedupes():
    box = FakeMailbox([
        message(),
        message(),  # the same post twice (e.g. a resend)
        message(subject="Hello", list_id="chinai.substack.com", list_url="https://chinai.substack.com/",
                html='<a href="https://chinai.substack.com/p/x">x</a>'),
        message(subject="814180 is your Substack verification code"),
    ])
    result = await driver(box).discover(EPOCH, PollState())
    assert [r.url for r in result.refs] == ["https://epochai.substack.com/p/glm-patch-gap"]
    assert result.refs[0].source_id == "epoch-ai"
    assert box.since == date(2026, 11, 1)


async def test_unchanged_mailbox_is_not_modified():
    box = FakeMailbox([message()])
    first = await driver(box).discover(EPOCH, PollState())
    again = await driver(box).discover(EPOCH, PollState(content_hash=first.content_hash))
    assert again.not_modified is True
    box.fingerprint = "v2"
    assert (await driver(box).discover(EPOCH, PollState(content_hash=first.content_hash))).not_modified is False


async def test_lookback_zero_reads_everything():
    box = FakeMailbox([])
    await driver(box, lookback=0).discover(EPOCH, PollState())
    assert box.since is None


async def test_unconfigured_or_misconfigured_sources_fail_clearly():
    with pytest.raises(RuntimeError, match="IMAP_USER"):
        await EmailDriver(None).discover(EPOCH, PollState())
    no_list = EPOCH.model_copy(update={"params": {}})
    with pytest.raises(ValueError, match="list_id"):
        await driver(FakeMailbox([])).discover(no_list, PollState())


async def test_fetch_html_never_goes_to_the_web():
    _, ref = parse_post(message())
    assert await driver(FakeMailbox([])).fetch_html(ref) == ""
