"""Profile versioning, the proposal lifecycle, and the rating counter."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.state import StateStore
from schemas.article import ArticleRef, Summary
from schemas.scoring import Score
from schemas.source import SourceConfig

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(hours=1)
CFG = SourceConfig(id="acx", type="substack", name="ACX", feed_url="https://x/feed")


async def _article(store: StateStore, guid: str) -> int:
    ref = ArticleRef(source_id="acx", guid=guid, url=f"https://x/{guid}",
                     title=f"T{guid}", published_at=NOW)
    article_id = await store.ingest_article(ref, ref.url, NOW)
    await store.save_extraction(article_id, "text", 500, "<p/>", NOW)
    await store.save_summary(
        article_id,
        Summary(headline=f"H{guid}", bullets=["b"] * 5, model="m",
                prompt_version="summary-v3", categories=["ai"]),
        summarized_at=NOW,
    )
    return article_id


async def test_versions_are_sequential(store: StateStore) -> None:
    assert await store.create_profile_version("a", "stated", True) == "profile-v1"
    assert await store.create_profile_version("b", "stated", True) == "profile-v2"


async def test_an_approved_version_becomes_the_live_profile(store: StateStore) -> None:
    await store.create_profile_version("first", "stated", True)
    v2 = await store.create_profile_version("second", "stated", True)

    assert await store.latest_profile() == (v2, "second")


async def test_an_unapproved_version_is_not_live(store: StateStore) -> None:
    """The phase-3 read filter is what makes the review gate real."""
    await store.create_profile_version("live", "stated", True)
    await store.create_profile_version("proposed", "distilled", False)

    assert (await store.latest_profile())[1] == "live"


async def test_an_old_version_stays_readable(store: StateStore) -> None:
    """A score stamped with v1 must still resolve to the text that produced it."""
    v1 = await store.create_profile_version("original", "stated", True)
    await store.create_profile_version("replacement", "stated", True)

    assert await store.profile_body(v1) == "original"


async def test_approving_an_older_proposal_supersedes_a_newer_edit(
    store: StateStore,
) -> None:
    """Live-ness follows approval order, not creation order.

    A proposal is created before the reader hand-edits the profile but approved
    after, so ordering by ``created_at`` would make the approval a silent no-op:
    every signal reports success while the older hand-edit stays live.
    """
    await store.create_profile_version("original", "stated", True)
    proposal = await store.create_profile_version("proposal", "distilled", False)
    await store.create_profile_version("hand-edited", "stated", True)

    assert await store.resolve_proposal(proposal, approved=True) is True

    assert await store.latest_profile() == (proposal, "proposal")


async def test_versioning_survives_a_deleted_row(store: StateStore) -> None:
    """Numbering keys off the highest number, not the row count.

    ``main.py`` tells the operator to ``DELETE`` the seeded version to resolve a
    profile divergence. A count-derived N would then re-issue a number that is
    already taken, and every later insert would collide forever.
    """
    await store.seed_profile("profile-v1", "seeded")
    await store.create_profile_version("second", "distilled", True)

    await store.db.execute("DELETE FROM profile_versions WHERE version = 'profile-v1'")
    await store.db.commit()

    assert await store.create_profile_version("third", "distilled", True) == "profile-v3"


async def test_an_empty_edit_is_not_the_same_as_no_edit(store: StateStore) -> None:
    """``None`` means "no edit"; ``""`` is an edit to empty text.

    ``COALESCE`` cannot tell them apart on its own, so the distinction is pinned
    here. Rejecting a blank body is a route's job, not the store's.
    """
    v = await store.create_profile_version("proposed", "distilled", False)
    await store.resolve_proposal(v, approved=True)
    assert await store.profile_body(v) == "proposed"

    blank = await store.create_profile_version("proposed", "distilled", False)
    await store.resolve_proposal(blank, approved=True, body="")
    assert await store.profile_body(blank) == ""


async def test_pending_proposal_is_found(store: StateStore) -> None:
    await store.create_profile_version("live", "stated", True)
    v = await store.create_profile_version("proposed", "distilled", False)

    pending = await store.pending_proposal()
    assert pending["version"] == v
    assert pending["body"] == "proposed"


async def test_no_pending_proposal_when_all_resolved(store: StateStore) -> None:
    await store.create_profile_version("live", "stated", True)

    assert await store.pending_proposal() is None


async def test_approving_makes_the_proposal_live(store: StateStore) -> None:
    await store.create_profile_version("live", "stated", True)
    v = await store.create_profile_version("proposed", "distilled", False)

    assert await store.resolve_proposal(v, approved=True) is True

    assert await store.latest_profile() == (v, "proposed")
    assert await store.pending_proposal() is None


async def test_approving_with_an_edited_body_stores_the_edit(store: StateStore) -> None:
    """The one legal mutation: a pending row has nothing pointing at it."""
    await store.create_profile_version("live", "stated", True)
    v = await store.create_profile_version("proposed", "distilled", False)

    await store.resolve_proposal(v, approved=True, body="edited by hand")

    assert await store.latest_profile() == (v, "edited by hand")


async def test_an_edited_proposal_is_still_distilled(store: StateStore) -> None:
    """It originated from distillation; the reader amended it."""
    v = await store.create_profile_version("proposed", "distilled", False)
    await store.resolve_proposal(v, approved=True, body="edited")

    cur = await store.db.execute(
        "SELECT kind FROM profile_versions WHERE version = ?", (v,)
    )
    assert (await cur.fetchone())["kind"] == "distilled"


async def test_rejecting_leaves_the_previous_profile_live(store: StateStore) -> None:
    await store.create_profile_version("live", "stated", True)
    v = await store.create_profile_version("proposed", "distilled", False)

    assert await store.resolve_proposal(v, approved=False) is True

    assert (await store.latest_profile())[1] == "live"
    assert await store.pending_proposal() is None


async def test_a_rejected_proposal_is_retained(store: StateStore) -> None:
    """Section 2.5: the record and the counter's reset point both live here."""
    v = await store.create_profile_version("proposed", "distilled", False)
    await store.resolve_proposal(v, approved=False)

    cur = await store.db.execute(
        "SELECT body, rejected_at FROM profile_versions WHERE version = ?", (v,)
    )
    row = await cur.fetchone()
    assert row["body"] == "proposed"
    assert row["rejected_at"] is not None


async def test_resolving_twice_reports_false(store: StateStore) -> None:
    v = await store.create_profile_version("proposed", "distilled", False)
    await store.resolve_proposal(v, approved=True)

    assert await store.resolve_proposal(v, approved=True) is False


async def test_resolving_an_unknown_version_reports_false(store: StateStore) -> None:
    assert await store.resolve_proposal("profile-v99", approved=True) is False
