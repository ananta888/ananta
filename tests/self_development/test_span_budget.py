"""Unit tests for agent/services/span_budget."""
from __future__ import annotations

import pytest

from agent.services.span_budget import clamp_spans, omission_marker


class TestClampSpans:
    def test_returns_input_unchanged_when_within_budget(self):
        spans = ["a", "b"]
        assert clamp_spans(spans, 2) == spans
        assert clamp_spans(spans, 5) is spans

    def test_clamps_to_leading_spans_when_over_budget(self):
        spans = ["a", "b", "c", "d"]
        result = clamp_spans(spans, 2)
        assert result == ["a", "b"]
        assert len(result) == 2

    def test_clamps_to_leading_spans_when_equal(self):
        spans = ["x", "y", "z"]
        assert clamp_spans(spans, 3) is spans

    def test_empty_spans_within_budget(self):
        assert clamp_spans([], 1) == []

    def test_raises_value_error_when_max_spans_less_than_one(self):
        with pytest.raises(ValueError):
            clamp_spans(["a"], 0)
        with pytest.raises(ValueError):
            clamp_spans(["a"], -1)


class TestOmissionMarker:
    def test_empty_string_when_max_spans_is_one(self):
        assert omission_marker(1) == ""

    def test_marker_fits_inside_max_spans_for_two_or_more(self):
        for max_spans in (2, 3, 4, 5, 10):
            marker = omission_marker(max_spans)
            assert marker != ""
            assert len(marker) <= max_spans

    def test_marker_is_short_ellipsis_for_reasonable_budgets(self):
        assert omission_marker(3) == "..."
        assert omission_marker(10) == "..."

    def test_raises_value_error_when_max_spans_less_than_one(self):
        with pytest.raises(ValueError):
            omission_marker(0)
        with pytest.raises(ValueError):
            omission_marker(-5)
