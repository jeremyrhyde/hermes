"""Extraction: prefer feed content, fall back to fetching the article."""

from __future__ import annotations

import pytest

import services.extraction as extraction
from services.extraction import ExtractionResult, extract_text

FULL_HTML = """
<html><body>
  <nav>Home About Subscribe</nav>
  <article>
    <h1>The First Post</h1>
    <p>This is the first paragraph of a genuinely substantial article body
       that contains enough words to be treated as real content.</p>
    <p>And a second paragraph, also with meaningful prose in it so the
       extractor has something to latch onto and score highly.</p>
  </article>
  <footer>Copyright 2026</footer>
</body></html>
"""


def test_extracts_body_and_drops_chrome() -> None:
    result = extract_text(FULL_HTML)

    assert isinstance(result, ExtractionResult)
    assert "first paragraph" in result.text
    assert "Subscribe" not in result.text
    assert "Copyright 2026" not in result.text
    assert result.word_count > 20


def test_empty_html_yields_empty_result() -> None:
    result = extract_text("")
    assert result.text == ""
    assert result.word_count == 0


def test_unextractable_html_does_not_raise() -> None:
    result = extract_text("<html><body><div></div></body></html>")
    assert result.word_count == 0


def test_word_count_matches_text() -> None:
    result = extract_text(FULL_HTML)
    assert result.word_count == len(result.text.split())


def test_internal_trafilatura_failure_yields_empty_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("trafilatura exploded")

    monkeypatch.setattr(extraction.trafilatura, "extract", boom)

    result = extract_text(FULL_HTML)

    assert result == ExtractionResult(text="", word_count=0)


@pytest.mark.parametrize("bad_input", [None, 42, [], {}, "", "   "])
def test_non_string_input_yields_empty_result(bad_input: object) -> None:
    result = extract_text(bad_input)  # type: ignore[arg-type]
    assert result == ExtractionResult(text="", word_count=0)
