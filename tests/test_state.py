"""StateStore: lifecycle, preferences, source state."""

from __future__ import annotations

from core.state import StateStore


async def test_start_applies_migrations(tmp_path) -> None:
    s = StateStore(str(tmp_path / "a.db"))
    await s.start()
    try:
        cur = await s.db.execute("PRAGMA user_version")
        (version,) = await cur.fetchone()
        assert version == 1
    finally:
        await s.close()


async def test_start_is_idempotent(tmp_path) -> None:
    """Restarting the server against an existing DB must not fail."""
    path = str(tmp_path / "b.db")
    for _ in range(2):
        s = StateStore(path)
        await s.start()
        await s.close()


async def test_preferences_roundtrip(store: StateStore) -> None:
    await store.set_preference("score_cutoff", "70")
    assert await store.get_preference("score_cutoff") == "70"

    await store.set_preference("score_cutoff", "85")
    assert await store.get_preference("score_cutoff") == "85"


async def test_get_preference_returns_default_when_absent(store: StateStore) -> None:
    assert await store.get_preference("missing", default="5") == "5"


async def test_seed_preference_does_not_overwrite(store: StateStore) -> None:
    """Seeding is first-write-wins so env defaults never clobber user edits."""
    await store.seed_preference("max_displayed", "5")
    await store.set_preference("max_displayed", "12")
    await store.seed_preference("max_displayed", "5")

    assert await store.get_preference("max_displayed") == "12"
