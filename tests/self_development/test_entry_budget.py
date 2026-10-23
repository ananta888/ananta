"""CMP-2701: Unit tests for agent.services.entry_budget helpers."""
from __future__ import annotations

import pytest

from agent.services.entry_budget import clamp_entries, omission_marker


def test_clamp_returns_unchanged_when_within_budget():
    entries = ["a", "b"]
    assert clamp_entries(entries, 2) is entries
    assert clamp_entries(["x", "y", "z"], 5) == ["x", "y", "z"]
    assert clamp_entries([], 1) == []


def test_clamp_returns_leading_slice_when_over_budget():
    entries = list(range(1, 8))
    result = clamp_entries(entries, 3)
    assert result == [1, 2, 3]
    assert len(result) == 3
    assert entries[3:] != []


def test_clamp_does_not_mutate_input():
    entries = ["a", "b", "c"]
    clamp_entries(entries, 2)
    assert entries == ["a", "b", "c"]


def test_clamp_raises_value_error_for_invalid_max_entries():
    with pytest.raises(ValueError, match="max_entries must be >= 1"):
        clamp_entries(["a"], 0)
    with pytest.raises(ValueError):
        clamp_entries(["a"], -3)


def test_marker_empty_when_less_than_two():
    assert omission_marker(1) == ""


def test_marker_fits_inside_budget_for_two_and_more():
    for max_entries in (2, 3, 4, 10):
        marker = omission_marker(max_entries)
        assert marker != ""
        assert len(marker) <= max_entries


def test_marker_raises_value_error_for_invalid_max_entries():
    with pytest.raises(ValueError, match="max_entries must be >= 1"):
        omission_marker(0)
    with pytest.raises(ValueError):
        omission_marker(-5)


def test_marker_is_short_ellipsis_for_reasonable_budgets():
    assert omission_marker(2) == ".."
    assert omission_marker(3) == "..."
    assert omission_marker(7) == "..."
