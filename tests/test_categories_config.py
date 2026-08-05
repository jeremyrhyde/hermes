"""Category vocabulary and filter configuration."""

from __future__ import annotations

import pytest

from config import load_sources_config

BASE_SOURCES = """
sources:
  - id: acx
    type: substack
    name: ACX
    feed_url: https://acx.example/feed
"""


def _write(tmp_path, body: str):
    path = tmp_path / "sources.yaml"
    path.write_text(body + BASE_SOURCES, encoding="utf-8")
    return path


def test_absent_categories_block_yields_empty_lists(tmp_path) -> None:
    """Existing sources.yaml files must keep working untouched."""
    cfg = load_sources_config(_write(tmp_path, ""))
    assert cfg.categories.vocabulary == []
    assert cfg.categories.filters == []
    assert [s.id for s in cfg.sources] == ["acx"]


def test_loads_vocabulary_and_filters(tmp_path) -> None:
    cfg = load_sources_config(_write(tmp_path, """
categories:
  vocabulary: [ai, robotics, finance, semiconductors]
  filters: [ai, robotics]
"""))
    assert cfg.categories.vocabulary == ["ai", "robotics", "finance", "semiconductors"]
    assert cfg.categories.filters == ["ai", "robotics"]


def test_entries_are_lowercased_and_deduped(tmp_path) -> None:
    """One canonical form, so every downstream consumer agrees."""
    cfg = load_sources_config(_write(tmp_path, """
categories:
  vocabulary: [AI, ai, Robotics]
  filters: [AI]
"""))
    assert cfg.categories.vocabulary == ["ai", "robotics"]
    assert cfg.categories.filters == ["ai"]


def test_filters_not_in_vocabulary_raises(tmp_path) -> None:
    """A filter with no vocabulary entry could never match anything."""
    with pytest.raises(ValueError, match="finance"):
        load_sources_config(_write(tmp_path, """
categories:
  vocabulary: [ai, robotics]
  filters: [ai, finance]
"""))


def test_vocabulary_without_filters_is_valid(tmp_path) -> None:
    """Tag everything, display no filter row — a legitimate state."""
    cfg = load_sources_config(_write(tmp_path, """
categories:
  vocabulary: [ai, robotics]
"""))
    assert cfg.categories.vocabulary == ["ai", "robotics"]
    assert cfg.categories.filters == []
