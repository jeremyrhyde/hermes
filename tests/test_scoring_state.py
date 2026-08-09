"""Scoring state: persistence, checkpoints, profile versions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.state import StateStore
from schemas.article import ArticleRef, Summary
from schemas.scoring import Score
from schemas.source import SourceConfig

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(hours=1)
CFG = SourceConfig(id="acx", type="substack", name="ACX", feed_url="https://x/feed")


def _score(value: int) -> Score:
    return Score(value=value, rationale="because", rubric_version="rubric-v1",
                 profile_version="profile-v1", signals={"inputs": ["bullets"]})


async def _article(store: StateStore, guid: str, *, summarize: bool = True) -> int:
    ref = ArticleRef(source_id="acx", guid=guid, url=f"https://x/{guid}",
                     title=f"T{guid}", published_at=NOW)
    article_id = await store.ingest_article(ref, ref.url, NOW)
    await store.save_extraction(article_id, "text", 500, "<p/>", NOW)
    if summarize:
        await store.save_summary(
            article_id,
            Summary(headline=f"H{guid}", bullets=["b"] * 5, model="m",
                    prompt_version="summary-v3", categories=["ai"]),
            summarized_at=NOW,
        )
    return article_id


async def test_saving_a_score_sets_the_checkpoint(store: StateStore) -> None:
    await store.upsert_source(CFG)
    article_id = await _article(store, "a")

    await store.save_score(article_id, _score(82), NOW)

    cur = await store.db.execute(
        "SELECT scored_at FROM articles WHERE id = ?", (article_id,)
    )
    assert (await cur.fetchone())["scored_at"] is not None


async def test_saved_score_appears_in_the_feed(store: StateStore) -> None:
    await store.upsert_source(CFG)
    article_id = await _article(store, "a")
    await store.save_score(article_id, _score(82), NOW)

    assert (await store.feed_items())[0]["score"] == 82


async def test_score_row_records_both_versions_and_signals(store: StateStore) -> None:
    await store.upsert_source(CFG)
    article_id = await _article(store, "a")
    await store.save_score(article_id, _score(82), NOW)

    cur = await store.db.execute(
        "SELECT rubric_version, profile_version, rationale, signals FROM scores"
    )
    row = await cur.fetchone()
    assert row["rubric_version"] == "rubric-v1"
    assert row["profile_version"] == "profile-v1"
    assert row["rationale"] == "because"
    assert "bullets" in row["signals"]


async def test_unscored_article_is_pending_score(store: StateStore) -> None:
    await store.upsert_source(CFG)
    article_id = await _article(store, "a")

    assert article_id in await store.articles_pending("score")


async def test_scored_article_is_not_pending_score(store: StateStore) -> None:
    await store.upsert_source(CFG)
    article_id = await _article(store, "a")
    await store.save_score(article_id, _score(50), NOW)

    assert article_id not in await store.articles_pending("score")


async def test_unsummarized_article_is_never_pending_score(store: StateStore) -> None:
    """The trap: reusing the summarize precondition would score a missing summary."""
    await store.upsert_source(CFG)
    article_id = await _article(store, "a", summarize=False)

    assert article_id not in await store.articles_pending("score")


async def test_unusable_article_is_never_pending_score(store: StateStore) -> None:
    await store.upsert_source(CFG)
    article_id = await _article(store, "a", summarize=False)
    await store.mark_unusable(article_id, "paywalled", NOW)

    assert article_id not in await store.articles_pending("score")


async def test_seeding_a_profile_then_reading_it_back(store: StateStore) -> None:
    await store.seed_profile("profile-v1", "I like cross-domain work")

    assert await store.latest_profile() == ("profile-v1", "I like cross-domain work")


async def test_seeding_never_clobbers_an_existing_profile(store: StateStore) -> None:
    """The file is a seed; the table is authoritative once phase 4 edits it."""
    await store.seed_profile("profile-v1", "original")
    await store.seed_profile("profile-v1", "overwritten")

    assert (await store.latest_profile())[1] == "original"


async def test_latest_profile_is_none_when_unseeded(store: StateStore) -> None:
    assert await store.latest_profile() is None


async def test_a_rescore_with_a_colliding_timestamp_still_wins(
    store: StateStore,
) -> None:
    """Both score tables are append-only, so "latest" needs a tie-break.

    Without `id DESC` a colliding `created_at` resolves by rowid *ascending*
    and the OLDER row wins — the re-score is silently discarded, which is the
    hardest kind of failure to notice. A phase-4 batch re-score stamping one
    `now` across the whole run is exactly how the collision arises.
    """
    await store.upsert_source(CFG)
    article_id = await _article(store, "a")

    await store.save_score(article_id, _score(10), NOW)
    await store.save_score(article_id, _score(90), NOW)  # same timestamp

    assert (await store.feed_items())[0]["score"] == 90


async def test_seeded_profiles_are_approved_on_write(store: StateStore) -> None:
    """A hand-written profile has nobody but its author to approve it."""
    await store.seed_profile("profile-v1", "body")

    cur = await store.db.execute("SELECT approved_at, kind FROM profile_versions")
    row = await cur.fetchone()
    assert row["approved_at"] is not None
    assert row["kind"] == "stated"


async def test_an_unapproved_profile_is_never_returned(store: StateStore) -> None:
    """Spec: a NULL approved_at means proposed, not active.

    Phase 4 proposes distilled versions for review. Without this filter the
    first proposal would become the live profile the moment it was written,
    silently changing what every later score means — which is precisely what
    the approval gate exists to prevent.
    """
    await store.seed_profile("profile-v1", "approved body")
    await store.db.execute(
        """
        INSERT INTO profile_versions (version, body, kind, created_at, approved_at)
        VALUES ('profile-v2', 'proposed body', 'distilled', ?, NULL)
        """,
        (LATER.isoformat(),),
    )
    await store.db.commit()

    assert (await store.latest_profile()) == ("profile-v1", "approved body")
