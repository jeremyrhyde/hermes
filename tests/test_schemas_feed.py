"""Feed schemas: additive-safe, with the extensibility hooks from spec section 12."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from schemas.article import Article, FeedItem, Summary
from schemas.source import SourceConfig, SourceRef


def test_source_config_params_are_open_ended() -> None:
    """Spec 12.6: a new source type adds keys to params, not fields to the model."""
    cfg = SourceConfig(
        id="acx", type="substack", name="ACX",
        feed_url="https://astralcodexten.substack.com/feed",
        params={"use_playwright": True, "future_key": [1, 2]},
    )
    assert cfg.params["use_playwright"] is True
    assert cfg.enabled is True


def test_models_ignore_unknown_fields() -> None:
    """Spec 12.1: reading a row written by a newer version must not explode."""
    cfg = SourceConfig.model_validate({
        "id": "acx", "type": "substack", "name": "ACX",
        "feed_url": "https://x.example/feed",
        "field_from_the_future": "surprise",
    })
    assert cfg.id == "acx"


def test_summary_requires_exactly_five_bullets() -> None:
    with pytest.raises(ValidationError):
        Summary(headline="H", bullets=["a", "b", "c"], model="m", prompt_version="v1")

    with pytest.raises(ValidationError):
        Summary(
            headline="H", bullets=["a"] * 6, model="m", prompt_version="v1"
        )

    ok = Summary(headline="H", bullets=["a"] * 5, model="m", prompt_version="v1")
    assert len(ok.bullets) == 5
    assert ok.metadata == {}


def test_feed_item_carries_source_ref() -> None:
    """Spec 12.5: source identity ships in the card from phase 1."""
    item = FeedItem(
        article_id=1,
        headline="H",
        bullets=["a"] * 5,
        url="https://x.example/p/1",
        published_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
        source=SourceRef(id="acx", name="ACX", type="substack"),
    )
    assert item.source.name == "ACX"
    assert item.score is None
    assert item.badges == []


def test_article_defaults_are_additive_safe() -> None:
    a = Article(
        id=1, source_id="acx", guid="g1",
        canonical_url="https://x.example/p/1", title="T",
        fetched_at=datetime.now(timezone.utc),
    )
    assert a.text is None
    assert a.metadata == {}
    assert a.summarized_at is None
