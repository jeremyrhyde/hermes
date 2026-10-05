"""Pantheon module contract: unique port, UI at `/`, API under `/api/`,
`/health` at the root, and only relative URLs in the web UI — so the same
files work standalone (http://pi:8002/) and behind Pantheon's gateway
(http://pi:8000/hermes/). See pantheon/docs/module-contract.md."""

from __future__ import annotations

from config import Settings


def test_default_port_is_8002(monkeypatch):
    monkeypatch.delenv("PORT", raising=False)
    assert Settings(_env_file=None).PORT == 8002
from pathlib import Path

from fastapi.testclient import TestClient

from main import build_app


def _settings(tmp_path: Path) -> Settings:
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<!doctype html><title>contract-test-ui</title>")
    return Settings(
        _env_file=None,
        WEB_DIR=str(web),
        DB_PATH=str(tmp_path / "t.db"),
        SOURCES_CONFIG_PATH=str(tmp_path / "none.yaml"),
        PROFILE_PATH=str(tmp_path / "none.md"),
    )


def test_every_route_is_under_api_or_is_health(tmp_path):
    # Newer FastAPI keeps included routers as lazy `_IncludedRouter` objects in
    # `app.routes`, so read the effective HTTP paths from the OpenAPI schema.
    app = build_app(_settings(tmp_path))
    paths = sorted(app.openapi()["paths"])
    stray = [p for p in paths if p != "/health" and not p.startswith("/api/")]
    assert stray == []
    assert "/api/feed/" in paths


def test_websocket_is_at_api_ws(tmp_path):
    # WebSocket routes are not in the OpenAPI schema; probe by connecting.
    client = TestClient(build_app(_settings(tmp_path)))  # no `with`: lifespan not run
    with client.websocket_connect("/api/ws"):
        pass


def test_ui_served_at_root_without_shadowing_health(tmp_path):
    client = TestClient(build_app(_settings(tmp_path)))  # no `with`: lifespan not run
    root = client.get("/")
    assert root.status_code == 200
    assert "contract-test-ui" in root.text
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/ui/").status_code == 404
