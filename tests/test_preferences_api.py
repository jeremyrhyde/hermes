"""Runtime knobs: read, write, validate."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from core.api import create_app
from core.events import EventBus
from core.state import StateStore
from core.websocket import WebSocketManager


@pytest.fixture
def client(store: StateStore) -> TestClient:
    bus = EventBus()
    ws = WebSocketManager()
    ws.subscribe_to_bus(bus)
    app = create_app(event_bus=bus, ws_manager=ws, state_store=store,
                     poller=None, settings=None, mount_static=False)
    return TestClient(app)


def test_preferences_report_defaults_when_unseeded(client: TestClient) -> None:
    body = client.get("/api/preferences/").json()
    assert body["score_cutoff"] == 0
    assert body["max_displayed"] == 50


def test_setting_a_preference_round_trips(client: TestClient) -> None:
    assert client.put("/api/preferences/score_cutoff", json={"value": 70}).status_code == 204
    assert client.get("/api/preferences/").json()["score_cutoff"] == 70


@pytest.mark.parametrize("value", [-1, 101])
def test_cutoff_outside_0_100_is_rejected(client: TestClient, value: int) -> None:
    """A typo here empties the feed, which reads as a broken deploy."""
    res = client.put("/api/preferences/score_cutoff", json={"value": value})
    assert res.status_code == 400


@pytest.mark.parametrize("value", [0, 201])
def test_max_displayed_outside_1_200_is_rejected(client: TestClient, value: int) -> None:
    res = client.put("/api/preferences/max_displayed", json={"value": value})
    assert res.status_code == 400


def test_unknown_preference_key_is_rejected(client: TestClient) -> None:
    res = client.put("/api/preferences/nonsense", json={"value": 1})
    assert res.status_code == 400
    assert "nonsense" in res.json()["detail"]


def test_api_defaults_mirror_the_settings_seeds() -> None:
    """The unseeded fallback and the seeded value must be the same number.

    `_PREFERENCES` supplies the value when no row exists; `Settings` supplies
    the value the seeder writes on first run. They are two hardcoded copies of
    one decision, and the mandated default tests above assert literals — so
    changing `config.py` alone would leave the suite green while an unseeded
    app disagreed with a seeded one.
    """
    from config import Settings
    from core.api import (
        PREF_DISTILL_THRESHOLD,
        PREF_MAX_DISPLAYED,
        PREF_SCORE_CUTOFF,
        _PREFERENCES,
    )

    settings = Settings()
    assert _PREFERENCES[PREF_SCORE_CUTOFF].default == settings.DEFAULT_SCORE_CUTOFF
    assert _PREFERENCES[PREF_MAX_DISPLAYED].default == settings.DEFAULT_MAX_DISPLAYED
    assert _PREFERENCES[PREF_DISTILL_THRESHOLD].default == settings.DEFAULT_DISTILL_THRESHOLD
