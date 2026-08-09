"""Scoring: rubric versioning, tool schema, range validation, stamping."""

from __future__ import annotations

import pytest

from services.rubric import RUBRIC, RUBRIC_VERSION


def test_rubric_version_is_pinned() -> None:
    assert RUBRIC_VERSION == "rubric-v1"


def test_rubric_demands_lens_interaction_not_mere_breadth() -> None:
    """Section 2.6: naive multi-domain scoring ranks a link roundup first."""
    text = RUBRIC.lower()
    assert "interact" in text or "changes the conclusion" in text


def test_rubric_requires_the_rationale_to_name_the_lenses() -> None:
    """Section 11.5: the only defense against a fabricated interaction."""
    assert "rationale" in RUBRIC.lower()


def test_rubric_is_a_frozen_constant_not_a_template() -> None:
    """No runtime interpolation: an edit must be a git diff and a version bump."""
    assert "{" not in RUBRIC and "}" not in RUBRIC
