"""URL canonicalization: strip tracking params before the uniqueness check."""

from __future__ import annotations

import pytest

from services.urls import canonicalize_url


@pytest.mark.parametrize(
    "raw,expected",
    [
        (
            "https://acx.substack.com/p/post?utm_source=twitter&utm_medium=social",
            "https://acx.substack.com/p/post",
        ),
        (
            "https://acx.substack.com/p/post?fbclid=abc123",
            "https://acx.substack.com/p/post",
        ),
        (
            "https://acx.substack.com/p/post?page=2&utm_campaign=x",
            "https://acx.substack.com/p/post?page=2",
        ),
        (
            "HTTPS://ACX.Substack.com/p/post",
            "https://acx.substack.com/p/post",
        ),
        (
            "https://acx.substack.com/p/post#section",
            "https://acx.substack.com/p/post",
        ),
        (
            "https://acx.substack.com/p/post/",
            "https://acx.substack.com/p/post",
        ),
    ],
)
def test_canonicalize_strips_noise(raw: str, expected: str) -> None:
    assert canonicalize_url(raw) == expected


def test_meaningful_params_survive() -> None:
    url = "https://example.com/search?q=hermes&sort=new"
    assert canonicalize_url(url) == url


def test_two_tagged_variants_collapse_to_one() -> None:
    a = canonicalize_url("https://acx.substack.com/p/post?utm_source=rss")
    b = canonicalize_url("https://acx.substack.com/p/post?utm_source=email&fbclid=z")
    assert a == b


def test_root_path_is_preserved() -> None:
    assert canonicalize_url("https://example.com/") == "https://example.com/"


def test_substack_mixed_case_share_tags_are_stripped() -> None:
    url = "https://acx.substack.com/p/post?isFreemail=true&triedRedirect=true"
    assert canonicalize_url(url) == "https://acx.substack.com/p/post"


@pytest.mark.parametrize(
    "raw,expected",
    [
        (
            "https://acx.substack.com/p/post?post_id=456&publication_id=123",
            "https://acx.substack.com/p/post",
        ),
        (
            "https://substack.com/p/post?post_id=456&publication_id=123",
            "https://substack.com/p/post",
        ),
    ],
)
def test_substack_share_tags_stripped_on_substack_hosts(raw: str, expected: str) -> None:
    assert canonicalize_url(raw) == expected


def test_post_id_preserved_on_non_substack_host() -> None:
    url = "https://blog.example.com/?post_id=123"
    assert canonicalize_url(url) == url


def test_publication_id_preserved_on_non_substack_host() -> None:
    url = "https://blog.example.com/?publication_id=123"
    assert canonicalize_url(url) == url


def test_post_id_not_stripped_on_lookalike_host() -> None:
    url = "https://substack.com.evil.example/p/post?post_id=456"
    assert canonicalize_url(url) == url


def test_realistic_substack_share_url_reduces_to_bare_slug() -> None:
    url = (
        "https://acx.substack.com/p/slug?utm_source=post-email-title"
        "&publication_id=123&post_id=456&isFreemail=true&triedRedirect=true"
    )
    assert canonicalize_url(url) == "https://acx.substack.com/p/slug"


def test_query_param_order_is_preserved() -> None:
    url = "https://example.com/search?b=2&a=1"
    assert canonicalize_url(url) == url


@pytest.mark.parametrize(
    "raw",
    [
        "https://example.com/search?q=hello world",
        "https://example.com/search?q=a%2Bb",
        "https://example.com/search?q=%E2%9C%93",
    ],
)
def test_canonicalize_is_idempotent(raw: str) -> None:
    once = canonicalize_url(raw)
    twice = canonicalize_url(once)
    assert once == twice
