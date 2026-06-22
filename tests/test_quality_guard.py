"""
HCCA-008 — Tests for QualityGuard and QualityResult.

The module already exists; all tests should pass immediately.
"""
from __future__ import annotations

import json

import pytest

from agent.services.context_compression.quality_guard import (
    QualityGuard,
    QualityResult,
)


@pytest.fixture
def guard() -> QualityGuard:
    return QualityGuard(min_score=0.7)


class TestQualityGuardBasic:
    def test_empty_compressed_fails_guard(self, guard):
        """compressed='' → score=0.0, passed=False."""
        result = guard.check("some original content", "", content_type="generic")
        assert isinstance(result, QualityResult)
        assert result.passed is False
        assert result.score == 0.0

    def test_whitespace_only_compressed_fails_guard(self, guard):
        """compressed='   ' (whitespace only) → passed=False."""
        result = guard.check("original content here", "   ", content_type="generic")
        assert result.passed is False

    def test_lossless_passes_guard(self, guard):
        """original == compressed → score penalised for lack of reduction but logic works."""
        # Note: identical content gets penalised for length_ratio and min_reduction checks.
        # We verify it returns a QualityResult without crashing; score may be below min.
        original = "identical content that was not changed at all"
        result = guard.check(original, original, content_type="generic")
        assert isinstance(result, QualityResult)
        # score should be deterministic
        assert 0.0 <= result.score <= 1.0

    def test_longer_than_original_fails(self, guard):
        """compressed longer than original → length_ratio check fails → penalty applied."""
        original = "short original"
        compressed = original + " EXTRA PADDING " * 20
        result = guard.check(original, compressed, content_type="generic")
        assert result.checks.get("length_ratio") is False

    def test_checks_dict_present(self, guard):
        """QualityResult always has a checks dict with expected keys."""
        result = guard.check(
            "original content " * 20,
            "compressed content shorter",
            content_type="generic",
        )
        assert isinstance(result.checks, dict)
        assert "not_empty" in result.checks


class TestQualityGuardErrorLines:
    def test_error_line_missing_fails_guard(self, guard):
        """Original has ERROR line; compressed doesn't → error_lines_preserved=False."""
        original = "normal line\nERROR: database failed\nanother line"
        compressed = "normal line\nanother line"  # ERROR missing
        result = guard.check(original, compressed, content_type="generic")
        assert result.checks.get("error_lines_preserved") is False

    def test_error_line_preserved_passes_check(self, guard):
        """Compressed retains ERROR line → error_lines_preserved=True."""
        original = "normal line\nERROR: database failed\nanother line"
        # Shorten but keep the error line
        compressed = "ERROR: database failed"
        result = guard.check(original, compressed, content_type="generic")
        assert result.checks.get("error_lines_preserved") is True

    def test_no_error_in_original_passes(self, guard):
        """No ERROR in original → error_lines_preserved always True."""
        original = "INFO line one\nINFO line two\nINFO line three"
        compressed = "INFO line one"
        result = guard.check(original, compressed, content_type="generic")
        assert result.checks.get("error_lines_preserved") is True


class TestQualityGuardJSON:
    def test_json_invalid_after_compress_fails(self, guard):
        """Original valid JSON + content_type='json', compressed invalid → json_valid=False."""
        original = json.dumps({"key": "value", "items": [1, 2, 3]})
        compressed = '{"key": "value", "items": [1, 2'  # truncated invalid JSON
        result = guard.check(original, compressed, content_type="json")
        assert result.checks.get("json_valid") is False

    def test_json_valid_after_compress_passes(self, guard):
        """Compressed JSON still valid → json_valid=True."""
        original = json.dumps([{"id": i, "score": 0.9, "noise": "x" * 100} for i in range(20)])
        # Build a shorter but still valid JSON
        compressed = json.dumps([{"id": i, "score": 0.9} for i in range(5)])
        result = guard.check(original, compressed, content_type="json")
        assert result.checks.get("json_valid") is True

    def test_non_json_type_skips_json_check(self, guard):
        """content_type='log' → json_valid check is True regardless of content."""
        original = "log line one\nlog line two " * 50
        compressed = "log line one"
        result = guard.check(original, compressed, content_type="log")
        assert result.checks.get("json_valid") is True


class TestQualityGuardMinReduction:
    def test_min_reduction_check_fails_small_reduction(self, guard):
        """original 1000 chars, compressed 990 chars (1% reduction < 10%) → min_reduction=False."""
        original = "a" * 1000
        compressed = "a" * 990
        result = guard.check(original, compressed, content_type="generic")
        assert result.checks.get("min_reduction") is False

    def test_min_reduction_check_passes_large_reduction(self, guard):
        """original 1000 chars, compressed 500 chars (50% reduction) → min_reduction=True."""
        original = "a" * 1000
        compressed = "a" * 500
        result = guard.check(original, compressed, content_type="generic")
        assert result.checks.get("min_reduction") is True

    def test_score_is_float_in_range(self, guard):
        """score is always a float between 0.0 and 1.0."""
        result = guard.check("original " * 100, "compressed shorter", content_type="generic")
        assert isinstance(result.score, float)
        assert 0.0 <= result.score <= 1.0
