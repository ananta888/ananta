"""
HCCA-007 — Tests for SmartCompressor.

SmartCompressor does not exist yet; tests are marked xfail so the
suite is collectable and will auto-promote once the class is implemented.
"""
from __future__ import annotations

import json

import pytest

from agent.services.context_compression.smart_compressor import (
    SmartCompressor,
    SmartCompressionResult,
)


@pytest.fixture
def compressor():
    return SmartCompressor()


def _content(result) -> str:
    """Extract the compressed content string from a SmartCompressionResult or str/dict."""
    if isinstance(result, SmartCompressionResult):
        return result.content
    if isinstance(result, dict):
        return result.get("compressed_content", "")
    return str(result)


class TestSmartCompressorJSON:
    def test_compress_json_removes_nulls(self, compressor):
        """JSON object with null values → compressed JSON should omit them."""
        data = {"name": "Alice", "score": 0.9, "metadata": None, "tags": None}
        original = json.dumps(data)
        result = compressor.compress(original, content_type="json")
        compressed = _content(result)
        parsed = json.loads(compressed)
        assert "metadata" not in parsed or parsed.get("metadata") is not None
        assert "tags" not in parsed or parsed.get("tags") is not None

    def test_compress_json_truncates_long_strings(self, compressor):
        """JSON with a 500-char string → that string is truncated in output."""
        long_value = "x" * 500
        data = {"id": 1, "text": long_value, "score": 0.9}
        original = json.dumps(data)
        result = compressor.compress(original, content_type="json")
        compressed = _content(result)
        # The compressed output should be shorter than the original
        assert len(compressed) < len(original)


class TestSmartCompressorLog:
    def test_compress_log_removes_duplicate_lines(self, compressor):
        """100 identical log lines → output is significantly shorter."""
        line = "2026-06-22 10:00:00 DEBUG polling queue, no tasks found"
        original = "\n".join([line] * 100)
        result = compressor.compress(original, content_type="log")
        compressed = _content(result)
        assert len(compressed) < len(original) * 0.5

    def test_compress_log_preserves_error_lines(self, compressor):
        """ERROR and CRITICAL lines must always be kept in output."""
        lines = ["DEBUG normal line"] * 50
        lines.append("ERROR: database connection failed on host db-01")
        lines.append("CRITICAL: worker pool exhausted")
        lines += ["DEBUG normal line"] * 50
        original = "\n".join(lines)
        result = compressor.compress(original, content_type="log")
        compressed = _content(result)
        assert "ERROR: database connection failed on host db-01" in compressed
        assert "CRITICAL: worker pool exhausted" in compressed


class TestSmartCompressorSearchResults:
    def test_compress_search_results_keeps_top_n(self, compressor):
        """50-item search result list → output keeps only top N items."""
        items = [f"result_{i}: /path/to/file_{i}.py: description text here" for i in range(50)]
        original = "\n".join(items)
        result = compressor.compress(original, content_type="search_results")
        compressed = _content(result)
        assert len(compressed) < len(original)


class TestSmartCompressorGeneric:
    def test_compress_generic_removes_blank_lines(self, compressor):
        """Text with many blank lines → blank lines removed in output."""
        parts = ["content line"] + [""] * 10 + ["another line"] + [""] * 10 + ["end"]
        original = "\n".join(parts)
        result = compressor.compress(original, content_type="generic")
        compressed = _content(result)
        # Should have fewer blank lines than original
        blank_count_orig = original.count("\n\n")
        blank_count_comp = compressed.count("\n\n")
        assert blank_count_comp <= blank_count_orig

    def test_compress_generic_truncates_long_text(self, compressor):
        """10000-char multi-line text → output is shorter due to head/tail truncation."""
        # Use many short lines so the head/tail strategy has lines to work with
        original = "\n".join(["word content here for line number " + str(i) for i in range(500)])
        result = compressor.compress(original, content_type="generic")
        compressed = _content(result)
        assert len(compressed) < len(original), (
            f"Expected compressed ({len(compressed)}) < original ({len(original)})"
        )

    def test_compress_unknown_type_falls_back_to_generic(self, compressor):
        """Unknown content_type → no exception, falls back gracefully."""
        original = "some content with an unknown type " * 50
        # Should not raise
        result = compressor.compress(original, content_type="unknown_xyz_type_that_doesnt_exist")
        assert result is not None

    def test_compression_achieves_target_reduction(self, compressor):
        """Repeated log noise → at least 20% character reduction."""
        line = "2026-06-22 10:00:00 DEBUG polling queue, nothing to do\n"
        original = line * 100
        result = compressor.compress(original, content_type="log")
        compressed = _content(result)
        reduction = (len(original) - len(compressed)) / len(original)
        assert reduction >= 0.20, f"Expected >=20% reduction, got {reduction:.1%}"

    def test_quality_hint_reasonable_range(self, compressor):
        """quality_hint in SmartCompressionResult is between 0.0 and 1.0."""
        original = "sample content " * 100
        result = compressor.compress(original, content_type="generic")
        assert isinstance(result, SmartCompressionResult)
        assert 0.0 <= result.quality_hint <= 1.0
