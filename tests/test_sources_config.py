"""sources.yaml loading."""

from __future__ import annotations

from config import load_sources_config


def test_missing_file_yields_empty_config(tmp_path) -> None:
    """Boot with nothing configured, matching the scaffold's posture."""
    cfg = load_sources_config(tmp_path / "nope.yaml")
    assert cfg.sources == []


def test_loads_and_validates(tmp_path) -> None:
    path = tmp_path / "sources.yaml"
    path.write_text(
        """
sources:
  - id: acx
    type: substack
    name: Astral Codex Ten
    feed_url: https://astralcodexten.substack.com/feed
  - id: platformer
    type: substack
    name: Platformer
    feed_url: https://www.platformer.news/feed
    enabled: false
    params:
      note: arbitrary driver-specific data
""",
        encoding="utf-8",
    )

    cfg = load_sources_config(path)

    assert [s.id for s in cfg.sources] == ["acx", "platformer"]
    assert cfg.sources[1].enabled is False
    assert cfg.sources[1].params["note"] == "arbitrary driver-specific data"


def test_empty_file_yields_empty_config(tmp_path) -> None:
    path = tmp_path / "sources.yaml"
    path.write_text("", encoding="utf-8")
    assert load_sources_config(path).sources == []
