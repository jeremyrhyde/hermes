"""Seeding the taste profile from profile.md.

`main.py` had no test harness before this; these import the seeder directly
rather than booting the app, which is enough to pin the behavior that would
otherwise only be observable by running the server and reading /health.
"""

from __future__ import annotations

from pathlib import Path

from core.state import StateStore
from main import PROFILE_VERSION, _seed_profile_from_file


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

    Seeding is insert-if-absent so the file can never clobber a version the
    reader approved — but an operator who edits their taste, restarts, and sees
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
    assert "DELETE FROM profile_versions" in failures[0]["error"], (
        "the report must say how to adopt the edit, not merely that it was ignored"
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

    await store.db.execute(
        """
        INSERT INTO profile_versions (version, body, kind, created_at, approved_at)
        VALUES ('profile-v2-distilled', 'learned taste', 'distilled', ?, ?)
        """,
        ("2026-09-01T00:00:00+00:00", "2026-09-01T00:00:00+00:00"),
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
