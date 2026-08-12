"""Seeding the taste profile from profile.md.

`main.py` had no test harness before this; these import the seeder directly
rather than booting the app, which is enough to pin the behavior that would
otherwise only be observable by running the server and reading /health.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from core.state import StateStore
from main import PROFILE_SEED_MARKER, PROFILE_VERSION, _seed_profile_from_file


async def test_a_missing_profile_is_silent(store: StateStore, tmp_path: Path) -> None:
    """Absent means scoring is off, not that anything went wrong."""
    failures: list[dict] = []

    await _seed_profile_from_file(store, str(tmp_path / "nope.md"), failures)

    assert failures == []
    assert await store.latest_profile() is None


async def test_a_profile_is_seeded_and_readable(store: StateStore, tmp_path: Path) -> None:
    path = tmp_path / "profile.md"
    path.write_text("I like cross-domain work", encoding="utf-8")
    failures: list[dict] = []

    await _seed_profile_from_file(store, str(path), failures)

    assert failures == []
    assert await store.latest_profile() == (PROFILE_VERSION, "I like cross-domain work")


async def test_an_empty_profile_is_reported(store: StateStore, tmp_path: Path) -> None:
    """A blank profile would score every article against nothing."""
    path = tmp_path / "profile.md"
    path.write_text("   \n\n", encoding="utf-8")
    failures: list[dict] = []

    await _seed_profile_from_file(store, str(path), failures)

    assert [f["component"] for f in failures] == ["profile"]
    assert await store.latest_profile() is None


async def test_editing_the_file_after_seeding_is_reported_not_silent(
    store: StateStore, tmp_path: Path
) -> None:
    """The edit is ignored by design; ignoring it silently is the bug.

    Seeding inserts only while no profile is approved, so the file can never
    clobber one the reader approved — but an operator who edits their taste,
    restarts, and sees
    identical scores has no reason to suspect the file was ignored rather than
    the rubric being unmoved. The stored profile must win AND say so.
    """
    path = tmp_path / "profile.md"
    path.write_text("original", encoding="utf-8")
    await _seed_profile_from_file(store, str(path), [])

    path.write_text("edited", encoding="utf-8")
    failures: list[dict] = []
    await _seed_profile_from_file(store, str(path), failures)

    assert (await store.latest_profile())[1] == "original", "the file must not clobber"
    assert [f["component"] for f in failures] == ["profile"]
    assert "differs" in failures[0]["error"]
    remedy = f"DELETE FROM profile_versions WHERE version = '{PROFILE_VERSION}';"
    assert remedy in failures[0]["error"], (
        "the report must say how to adopt the edit, not merely that it was "
        "ignored — and the DELETE must name the seeded row specifically, since "
        "every other version is text the file has no claim on. Asserting only "
        "that PROFILE_VERSION appears somewhere in the message does not pin "
        "this: the sentence above the remedy names it too."
    )


async def test_reseeding_an_unchanged_file_is_silent(
    store: StateStore, tmp_path: Path
) -> None:
    """Every restart re-seeds; an unchanged file must not cry wolf."""
    path = tmp_path / "profile.md"
    path.write_text("same text", encoding="utf-8")

    await _seed_profile_from_file(store, str(path), [])
    failures: list[dict] = []
    await _seed_profile_from_file(store, str(path), failures)

    assert failures == []


async def test_an_approved_distillation_does_not_fake_a_divergence(
    store: StateStore, tmp_path: Path
) -> None:
    """The phase-4 case: an untouched file must stay silent once taste evolves.

    Comparing against "whichever profile is in effect" breaks here. Phase 4
    appends approved distilled versions and the newest wins, so an unedited
    profile.md would look changed forever — and the remedy would name the
    distilled row, whose deletion destroys the only stored copy of the text
    every score stamped with it was judged against.
    """
    path = tmp_path / "profile.md"
    path.write_text("stated taste", encoding="utf-8")
    await _seed_profile_from_file(store, str(path), [])

    # Stamped one second after the row seeding just wrote, rather than at a
    # fixed date. `latest_profile` orders by `approved_at`, and `seed_profile`
    # stamps the real clock — so a hardcoded date silently stops being "later
    # than the seed" the moment the wall clock passes it, and the assertion
    # below flips to `profile-v1` with nothing about the test looking wrong.
    cur = await store.db.execute(
        "SELECT approved_at FROM profile_versions WHERE version = ?",
        (PROFILE_VERSION,),
    )
    seeded_at = datetime.fromisoformat((await cur.fetchone())["approved_at"])
    after_the_seed = (seeded_at + timedelta(seconds=1)).isoformat()

    await store.db.execute(
        """
        INSERT INTO profile_versions (version, body, kind, created_at, approved_at)
        VALUES ('profile-v2-distilled', 'learned taste', 'distilled', ?, ?)
        """,
        (after_the_seed, after_the_seed),
    )
    await store.db.commit()

    failures: list[dict] = []
    await _seed_profile_from_file(store, str(path), failures)

    assert failures == [], "an untouched file must not report a divergence"
    assert (await store.latest_profile())[0] == "profile-v2-distilled"


# Removed in phase 4: test_the_remedy_never_names_a_distilled_profile pinned the
# wording of a report that is no longer emitted in the case it set up. It seeded,
# approved a distilled version, edited the file, and required a failure naming
# the seeded row rather than the distilled one. The warning now stops once any
# later version is in effect, so that case is silent and the concern it guarded
# — a remedy naming phase 4's own output — cannot arise: the only path that
# still reports is the one where the seed is live, and
# test_editing_the_file_after_seeding_is_reported_not_silent pins its remedy.


async def test_divergence_is_silent_once_the_profile_has_moved_on(
    store: StateStore, tmp_path: Path
) -> None:
    """Section 2.2: the file is a first-run seed, not a live mirror.

    Without this, the first Settings edit makes the warning permanent — and its
    remedy, deleting the seeded row, would discard that edit.
    """
    path = tmp_path / "profile.md"
    path.write_text("seeded text", encoding="utf-8")
    await _seed_profile_from_file(store, str(path), [])

    await store.create_profile_version("edited in the UI", "stated", True)

    path.write_text("the file has drifted", encoding="utf-8")
    failures: list[dict] = []
    await _seed_profile_from_file(store, str(path), failures)

    assert failures == []


async def test_a_rejected_proposal_does_not_count_as_moving_on(
    store: StateStore, tmp_path: Path
) -> None:
    """A later row exists, but the seed is still what scoring uses.

    This is why the guard asks what is *in effect* rather than whether the seed
    is the only row. A resolved proposal reads like the profile has moved on and
    it has not: the rejected body was never live, the file's edits are still
    inert, and the remedy still names the row that is. Counting rows instead
    would swallow this edit silently — the exact failure the warning exists to
    prevent. The pending case works the same way; rejection is the one that
    looks settled.
    """
    path = tmp_path / "profile.md"
    path.write_text("original", encoding="utf-8")
    await _seed_profile_from_file(store, str(path), [])

    proposed = await store.create_profile_version("a proposal", "distilled", False)
    assert await store.resolve_proposal(proposed, approved=False)

    path.write_text("edited", encoding="utf-8")
    failures: list[dict] = []
    await _seed_profile_from_file(store, str(path), failures)

    assert [f["component"] for f in failures] == ["profile"]
    assert PROFILE_VERSION in failures[0]["error"]
    assert (await store.latest_profile())[0] == PROFILE_VERSION, (
        "the rejected body must never have been in effect"
    )


async def test_divergence_still_reported_while_only_the_seed_exists(
    store: StateStore, tmp_path: Path
) -> None:
    """The phase-3 behavior survives for the case it was written for."""
    path = tmp_path / "profile.md"
    path.write_text("original", encoding="utf-8")
    await _seed_profile_from_file(store, str(path), [])

    path.write_text("edited", encoding="utf-8")
    failures: list[dict] = []
    await _seed_profile_from_file(store, str(path), failures)

    assert [f["component"] for f in failures] == ["profile"]


async def test_following_the_remedy_actually_adopts_the_edit(
    store: StateStore, tmp_path: Path
) -> None:
    """The remedy is only advice if the next boot re-seeds.

    Deleting the seeded row is the whole instruction, and the operator runs it
    by hand — so a gate that then declines to re-seed turns the printed cure
    into the disease: no approved profile at all, scoring silently off, and
    nothing on screen to connect it to the DELETE they were told to run. A
    resolved proposal is the case that exposes it, because the row it leaves
    behind outlives the deletion.

    Worth naming what this test now blesses: the re-seed takes the deleted
    row's name, so ``profile-v1`` ends up stamped on two different bodies over
    an install's life. ``scores.profile_version`` is a name with no foreign
    key, so every score from before the DELETE now resolves to text it was
    never judged against. That is the remedy working as designed — adopting the
    file's edits is the whole point, and the alternative is a live profile that
    the file can never reach — but "which text was this score judged against"
    stops being answerable across it.
    """
    path = tmp_path / "profile.md"
    path.write_text("original", encoding="utf-8")
    await _seed_profile_from_file(store, str(path), [])
    proposed = await store.create_profile_version("a proposal", "distilled", False)
    assert await store.resolve_proposal(proposed, approved=False)

    path.write_text("edited", encoding="utf-8")
    failures: list[dict] = []
    await _seed_profile_from_file(store, str(path), failures)
    assert len(failures) == 1, "the divergence must be reported for this to matter"

    await store.db.execute(
        "DELETE FROM profile_versions WHERE version = ?", (PROFILE_VERSION,)
    )
    await store.db.commit()
    await _seed_profile_from_file(store, str(path), [])

    assert (await store.latest_profile())[1] == "edited"


async def test_a_settings_authored_profile_survives_a_file_appearing_later(
    store: StateStore, tmp_path: Path
) -> None:
    """The fresh-install path since Settings shipped: no profile.md at all.

    The reader's first Settings write is numbered ``profile-v1``, the same name
    seeding uses. A ``profile.md`` added afterwards — by the operator, or by
    restoring a backup — must not be able to reach that row: not by inserting
    over it, not by superseding it, and above all not by prescribing a remedy
    that deletes it. That remedy is followed by hand, so emitting it at all is
    the harm; the reader's only copy of their profile is the row it names.
    """
    written = await store.create_profile_version("READER TEXT", "stated", True)
    assert written == PROFILE_VERSION, "the collision this guards is real"

    path = tmp_path / "profile.md"
    path.write_text("file text", encoding="utf-8")
    failures: list[dict] = []
    await _seed_profile_from_file(store, str(path), failures)

    assert failures == [], "no remedy may name a row the reader authored"
    assert await store.latest_profile() == (PROFILE_VERSION, "READER TEXT")


async def test_a_file_appearing_later_never_becomes_the_live_profile(
    store: StateStore, tmp_path: Path
) -> None:
    """Seeding is insert-if-*nothing-exists*, not insert-if-name-is-free.

    With the reader's profile under some other name, an insert of
    ``profile-v1`` collides with nothing and carries a fresher ``approved_at``,
    so it wins ``latest_profile`` outright. Scoring would switch to the file's
    text with no row overwritten and nothing to notice.
    """
    await store.create_profile_version("first", "stated", True)
    reader = await store.create_profile_version("READER TEXT", "stated", True)
    await store.db.execute(
        "DELETE FROM profile_versions WHERE version = ?", (PROFILE_VERSION,)
    )
    await store.db.commit()

    path = tmp_path / "profile.md"
    path.write_text("file text", encoding="utf-8")
    failures: list[dict] = []
    await _seed_profile_from_file(store, str(path), failures)

    assert await store.latest_profile() == (reader, "READER TEXT")
    assert failures == []


async def test_a_seed_from_before_the_marker_reports_nothing(
    store: StateStore, tmp_path: Path
) -> None:
    """An unmarked row whose body has already diverged stays silent.

    Nothing on the row distinguishes a seed from text the reader wrote in
    Settings — same ``kind``, same shape — and by the time the bodies differ
    the backfill below can no longer tell them apart either. Guessing wrong
    here costs the reader a ``DELETE`` aimed at their own profile, so an
    install that reaches this state unmarked stays silent permanently.
    """
    path = tmp_path / "profile.md"
    path.write_text("original", encoding="utf-8")
    await _seed_profile_from_file(store, str(path), [])
    await store.db.execute(
        "DELETE FROM preferences WHERE key = ?", (PROFILE_SEED_MARKER,)
    )
    await store.db.commit()

    path.write_text("edited", encoding="utf-8")
    failures: list[dict] = []
    await _seed_profile_from_file(store, str(path), failures)

    assert failures == []
    assert (await store.latest_profile())[1] == "original"


async def test_a_profile_written_after_the_remedy_is_never_named_again(
    store: StateStore, tmp_path: Path
) -> None:
    """The remedy sets up the very bug it was implicated in, one boot later.

    Delete now, restart later, use the app in between: the operator runs the
    prescribed DELETE, the reader writes a profile in Settings before the
    restart, and — numbering off an empty table — it is named ``profile-v1``
    again. A marker that only remembers the *file* still matches, so the next
    boot prints a DELETE for the row the reader just wrote. The marker has to
    identify the row it was written for, not the path it was read from.
    """
    path = tmp_path / "profile.md"
    path.write_text("original", encoding="utf-8")
    await _seed_profile_from_file(store, str(path), [])
    path.write_text("edited", encoding="utf-8")
    await _seed_profile_from_file(store, str(path), [])

    await store.db.execute(
        "DELETE FROM profile_versions WHERE version = ?", (PROFILE_VERSION,)
    )
    await store.db.commit()
    reader = await store.create_profile_version("MY HAND-WRITTEN TASTE", "stated", True)
    assert reader == PROFILE_VERSION, "the collision this guards is real"

    failures: list[dict] = []
    await _seed_profile_from_file(store, str(path), failures)

    assert failures == [], "no remedy may name a row the reader authored"
    assert await store.latest_profile() == (PROFILE_VERSION, "MY HAND-WRITTEN TASTE")


async def test_a_file_the_seed_name_blocks_is_reported(
    store: StateStore, tmp_path: Path
) -> None:
    """A readable, non-empty profile.md that can never load must say so.

    Reachable without a profile.md ever existing: the distiller proposes
    against an empty profile, so a reader who rates their way to a first
    distillation and rejects it leaves ``profile-v1`` held by an unapproved
    row. Nothing is approved, so seeding is willing — and the name is taken, so
    it silently does nothing. The file is then permanently inert, and the only
    other thing /health says is that no profile is loaded from ./profile.md,
    which reads as "the file is missing" and sends the reader after the wrong
    problem.
    """
    proposal = await store.create_profile_version("a proposal", "distilled", False)
    assert proposal == PROFILE_VERSION
    assert await store.resolve_proposal(proposal, approved=False)

    path = tmp_path / "profile.md"
    path.write_text("file text", encoding="utf-8")
    failures: list[dict] = []
    await _seed_profile_from_file(store, str(path), failures)

    assert [f["component"] for f in failures] == ["profile"]
    assert str(path) in failures[0]["error"]
    assert PROFILE_VERSION in failures[0]["error"]
    assert await store.latest_profile() is None


async def test_an_unmarked_seed_still_matching_its_file_is_adopted(
    store: StateStore, tmp_path: Path
) -> None:
    """The install this warning was built for is the one that predates markers.

    Marking a row byte-identical to the file costs nothing even if the reader
    wrote it: the harm only begins once the file is edited, and at that point
    the remedy adopts the edit and discards text the file already contains. So
    the one shape worth backfilling is the shape of a real seeded install — a
    single stated, approved ``profile-v1`` matching the file exactly — and
    without it the warning would have fired on no install at all while the
    silence it replaced covered every one.
    """
    path = tmp_path / "profile.md"
    path.write_text("original", encoding="utf-8")
    await _seed_profile_from_file(store, str(path), [])
    await store.db.execute(
        "DELETE FROM preferences WHERE key = ?", (PROFILE_SEED_MARKER,)
    )
    await store.db.commit()

    await _seed_profile_from_file(store, str(path), [])  # boot: file still matches
    path.write_text("edited", encoding="utf-8")
    failures: list[dict] = []
    await _seed_profile_from_file(store, str(path), failures)

    assert [f["component"] for f in failures] == ["profile"]
    assert "differs" in failures[0]["error"]


async def test_the_backfill_declines_when_the_row_is_not_alone(
    store: StateStore, tmp_path: Path
) -> None:
    """One row is what makes the shape unambiguous; anything else is a history.

    A second row means something happened on this install that a plain seed
    cannot explain, and the reader may have authored either row. The body
    matching proves nothing on its own — the file could have been written to
    match — so the backfill takes the whole shape or none of it.
    """
    path = tmp_path / "profile.md"
    path.write_text("original", encoding="utf-8")
    await _seed_profile_from_file(store, str(path), [])
    proposal = await store.create_profile_version("a proposal", "distilled", False)
    await store.resolve_proposal(proposal, approved=False)
    await store.db.execute(
        "DELETE FROM preferences WHERE key = ?", (PROFILE_SEED_MARKER,)
    )
    await store.db.commit()

    await _seed_profile_from_file(store, str(path), [])
    path.write_text("edited", encoding="utf-8")
    failures: list[dict] = []
    await _seed_profile_from_file(store, str(path), failures)

    assert failures == []


async def test_a_marker_in_the_first_shipped_format_can_still_be_adopted(
    store: StateStore, tmp_path: Path
) -> None:
    """The one unreadable marker we shipped must not be a permanent dead end.

    A marker written before it carried a row identity is a bare path string,
    which fails the parse and correctly disqualifies the row from a destructive
    remedy. Declining to *re-mark* it as well would leave those installs silent
    forever with nothing able to fix it, which is a worse upgrade path than the
    one the marker replaced.
    """
    path = tmp_path / "profile.md"
    path.write_text("original", encoding="utf-8")
    await _seed_profile_from_file(store, str(path), [])
    await store.set_preference(PROFILE_SEED_MARKER, str(path))  # pre-3f1740c format

    await _seed_profile_from_file(store, str(path), [])  # boot: file still matches
    path.write_text("edited", encoding="utf-8")
    failures: list[dict] = []
    await _seed_profile_from_file(store, str(path), failures)

    assert [f["component"] for f in failures] == ["profile"]


async def test_an_unreadable_marker_is_survivable_and_silent(
    store: StateStore, tmp_path: Path
) -> None:
    """Anything can be written into a preference; none of it may crash a boot.

    Nor may it warn. The marker is read to license a DELETE against the
    reader's data, so a value that cannot be understood has to read as "not
    ours" rather than raise or be waved through.
    """
    path = tmp_path / "profile.md"
    path.write_text("original", encoding="utf-8")
    await _seed_profile_from_file(store, str(path), [])
    await store.set_preference(PROFILE_SEED_MARKER, "{not json at all")

    path.write_text("edited", encoding="utf-8")
    failures: list[dict] = []
    await _seed_profile_from_file(store, str(path), failures)

    assert failures == []
    assert (await store.latest_profile())[1] == "original"


async def test_a_different_file_cannot_claim_another_file_s_seed(
    store: StateStore, tmp_path: Path
) -> None:
    """The row identity is half the marker; the file is the other half.

    A second profile.md — a copy under another name, a path change in the
    config — must not inherit the first one's seed. Every remedy is phrased as
    "your file's edits are being ignored", and pointed at a row this file never
    wrote it is simply false, with a DELETE attached.
    """
    seeded = tmp_path / "profile.md"
    seeded.write_text("original", encoding="utf-8")
    await _seed_profile_from_file(store, str(seeded), [])

    other = tmp_path / "elsewhere.md"
    other.write_text("a different file entirely", encoding="utf-8")
    failures: list[dict] = []
    await _seed_profile_from_file(store, str(other), failures)

    assert failures == []


async def test_reaching_the_same_file_by_another_path_still_reports(
    store: StateStore, tmp_path: Path, monkeypatch
) -> None:
    """The shipped default is relative, so the path alone is not the file.

    ``PROFILE_PATH`` defaults to ``./profile.md``; running the server from an
    absolute path, or from a symlinked directory, would otherwise look like a
    different file and drop the warning silently.
    """
    path = tmp_path / "profile.md"
    path.write_text("original", encoding="utf-8")
    await _seed_profile_from_file(store, str(path), [])

    path.write_text("edited", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    failures: list[dict] = []
    await _seed_profile_from_file(store, "profile.md", failures)

    assert [f["component"] for f in failures] == ["profile"]
