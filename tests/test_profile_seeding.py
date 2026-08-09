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
