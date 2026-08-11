"""What `_build_components` wires, and what it tells /health about it.

Hermetic: every path points at ``tmp_path``, the API key is fake, and no
component is ever driven — constructing an ``AsyncAnthropic`` makes no request.
The poller is stopped and the store closed before each test returns.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from config import Settings
from main import _build_components


def _settings(tmp_path: Path, *, api_key: str, profile: str | None) -> Settings:
    """Settings with every path under *tmp_path* and no live anything.

    Each field is passed explicitly — a `.env` in the repo root would otherwise
    supply the real key and the real database.
    """
    if profile is not None:
        (tmp_path / "profile.md").write_text(profile, encoding="utf-8")
    return Settings(
        DB_PATH=str(tmp_path / "test.db"),
        SOURCES_CONFIG_PATH=str(tmp_path / "sources.yaml"),
        PROFILE_PATH=str(tmp_path / "profile.md"),
        ANTHROPIC_API_KEY=api_key,
        POLL_TICK_SECONDS=3600.0,
    )


async def _build(settings: Settings):
    """Build components, hand them to the caller, tear them down after."""
    async with httpx.AsyncClient() as http:
        bus, ws, store, poller, distiller, vocab, filters, failures = (
            await _build_components(settings, http)
        )
        try:
            yield store, poller, failures
        finally:
            await poller.stop()
            await store.close()


@pytest.fixture
async def built(request, tmp_path: Path):
    """Parameterized by ``(api_key, profile)`` on the test's ``built`` marker."""
    api_key, profile = request.param
    settings = _settings(tmp_path, api_key=api_key, profile=profile)
    async for parts in _build(settings):
        yield parts


def _scorer_failure(failures: list[dict[str, Any]]) -> dict[str, Any] | None:
    entries = [f for f in failures if f["component"] == "scorer"]
    return entries[0] if entries else None


def _wired_scorer(poller):
    """The scorer the pipeline will actually use.

    Reached through privates deliberately: whether scoring has a path at all is
    a wiring fact with no public surface, and asserting it from /health instead
    would test the message rather than the behavior it describes.
    """
    return poller._pipeline._scorer


@pytest.mark.parametrize("built", [("sk-fake", None)], indirect=True)
async def test_a_missing_profile_no_longer_costs_a_restart(built) -> None:
    """The cold-start flow this phase itself creates must close without one.

    No profile.md, so the reader rates their way to a distillation and approves
    it. The pipeline reads the live profile once per run, so the only thing that
    could force a restart is refusing to build the scorer at boot.
    """
    _store, poller, _failures = built

    assert _wired_scorer(poller) is not None


@pytest.mark.parametrize("built", [("sk-fake", None)], indirect=True)
async def test_waiting_for_a_first_profile_is_not_reported_as_disabled(
    built,
) -> None:
    """Waiting and off are different states now, and the wording must differ.

    An operator who reads "scoring is disabled" goes looking for a switch to
    flip, and the honest answer is that approving a profile is the switch.
    """
    _store, _poller, failures = built

    entry = _scorer_failure(failures)
    assert entry is not None, "/health must still say why nothing is scored"
    assert "disabled" not in entry["error"]
    assert "restart" in entry["error"], (
        "the entry is a boot snapshot; it must say it stops applying by itself"
    )


@pytest.mark.parametrize("built", [("", None)], indirect=True)
async def test_no_key_still_reports_scoring_disabled(built) -> None:
    """Without a key there is no scoring path at all — that one really is off."""
    _store, poller, failures = built

    assert _wired_scorer(poller) is None
    entry = _scorer_failure(failures)
    assert entry is not None
    assert "disabled" in entry["error"]
    assert "ANTHROPIC_API_KEY" in entry["error"]


@pytest.mark.parametrize("built", [("sk-fake", "I like cross-domain work")], indirect=True)
async def test_a_key_and_a_profile_report_nothing(built) -> None:
    _store, poller, failures = built

    assert _wired_scorer(poller) is not None
    assert _scorer_failure(failures) is None


@pytest.mark.parametrize("built", [("sk-fake", None)], indirect=True)
async def test_waiting_for_a_profile_is_advisory_not_a_failure(built) -> None:
    """The one entry that is not a failure has to say so in the payload.

    /health's list is rendered under a headline that is read before any of the
    entries, and counting this one made a fresh install's first screen say "1
    component(s) failed to start" directly above text explaining that nothing
    is wrong and no restart is needed. `severity` is what lets the UI count and
    colour the two kinds apart.
    """
    _store, _poller, failures = built

    entry = _scorer_failure(failures)
    assert entry is not None
    assert entry["severity"] == "advisory"
    assert [f for f in failures if f.get("severity", "error") == "error"] == [], (
        "a key and no profile yet is the normal fresh-install state; nothing "
        "about it failed to start"
    )


@pytest.mark.parametrize("built", [("", None)], indirect=True)
async def test_things_that_really_did_not_start_are_errors(built) -> None:
    """And the ones that are failures must not be downgraded with it."""
    _store, _poller, failures = built

    assert {f["component"] for f in failures} == {"summarizer", "scorer"}
    assert [f["severity"] for f in failures] == ["error", "error"]


async def test_every_failure_entry_carries_a_severity(tmp_path: Path) -> None:
    """No entry may omit the field, whatever produced it.

    A missing `severity` reads as "error" downstream, which is the safe default
    but silently mislabels anything advisory added later. Broad on purpose: it
    covers the config and profile entries too, which the fixture above cannot
    reach.
    """
    (tmp_path / "sources.yaml").write_text("sources: [", encoding="utf-8")
    (tmp_path / "profile.md").write_text("   ", encoding="utf-8")
    settings = Settings(
        DB_PATH=str(tmp_path / "test.db"),
        SOURCES_CONFIG_PATH=str(tmp_path / "sources.yaml"),
        PROFILE_PATH=str(tmp_path / "profile.md"),
        ANTHROPIC_API_KEY="",
        POLL_TICK_SECONDS=3600.0,
    )
    async for _store, _poller, failures in _build(settings):
        assert {"profile", "sources_config", "summarizer", "scorer"} <= {
            f["component"] for f in failures
        }
        assert all(f["severity"] in {"error", "advisory"} for f in failures), failures
