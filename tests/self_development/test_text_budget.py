"""Unit tests for agent/services/text_budget (CMP-2301)."""
from __future__ import annotations

import pytest

from agent.services.text_budget import clamp_budget, truncation_marker


def test_clamp_budget_returns_unchanged_when_fits():
    assert clamp_budget("", 5) == ""
    assert clamp_budget("abc", 3) == "abc"
    assert clamp_budget("abc", 10) == "abc"


def test_clamp_budget_truncates_to_exact_length():
    text = "hello world"
    assert clamp_budget(text, 5) == "hello"
    assert len(clamp_budget(text, 5)) == 5
    assert clamp_budget("abcdef", 3) == "abc"
    # leading characters are kept
    assert clamp_budget("abcdef", 2) == "ab"


def test_clamp_budget_raises_value_error_when_max_chars_less_than_1():
    with pytest.raises(ValueError):
        clamp_budget("text", 0)
    with pytest.raises(ValueError):
        clamp_budget("text", -1)


def test_truncation_marker_empty_for_max_chars_below_3():
    assert truncation_marker(1) == ""
    assert truncation_marker(2) == ""


def test_truncation_marker_fits_inside_max_chars():
    marker = truncation_marker(3)
    assert marker == "..."
    assert len(marker) <= 3
    for n in (4, 5, 10):
        m = truncation_marker(n)
        assert len(m) <= n


def test_truncation_marker_raises_value_error_when_max_chars_less_than_1():
    with pytest.raises(ValueError):
        truncation_marker(0)
    with pytest.raises(ValueError):
        truncation_marker(-5)


def test_clamp_budget_and_marker_compose_within_budget():
    text = "a" * 20
    budget = 7
    clamped = clamp_budget(text, budget - len(truncation_marker(budget))) if budget >= 3 else clamp_budget(text, budget)
    marker = truncation_marker(budget)
    assert len(clamped) <= budget
    assert len(marker) <= budget
