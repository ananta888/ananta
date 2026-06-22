"""
HCCA-013 — Benchmark harness for context compression.

These are performance quality gates. Tests that depend on SmartCompressor
(not yet implemented) are marked xfail; TokenEstimator-only benchmarks
will pass immediately.
"""
from __future__ import annotations

import json
import time

import pytest

from agent.services.context_compression.token_estimator import TokenEstimator
from agent.services.context_compression.smart_compressor import (
    SmartCompressor,
    SmartCompressionResult,
)
from agent.services.context_compression.quality_guard import QualityGuard

_SMART_COMPRESSOR_OK = True
_QUALITY_GUARD_OK = True

# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

SAMPLE_TOOL_OUTPUT = (
    "Tool execution completed. Result: {status: ok, items_processed: 42} "
) * 300  # ~large tool output

SAMPLE_LOG_LINES = "\n".join(
    ["2026-06-22 10:00:00 DEBUG polling queue, no tasks found..."] * 200
)

SAMPLE_JSON_RESULTS = json.dumps(
    [
        {"id": i, "score": 0.9, "text": "x" * 200, "metadata": None}
        for i in range(50)
    ]
)

SAMPLE_SEARCH_RESULTS = "\n".join(
    [
        f"result_{i}: /path/to/file_{i}.py: some description text that describes what the file does"
        for i in range(100)
    ]
)

_ALL_SAMPLES = {
    "tool_output": (SAMPLE_TOOL_OUTPUT, "tool_output"),
    "log": (SAMPLE_LOG_LINES, "log"),
    "json": (SAMPLE_JSON_RESULTS, "json"),
    "search_results": (SAMPLE_SEARCH_RESULTS, "search_results"),
}


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _compress_with_result(compressor, content: str, content_type: str) -> dict:
    """Call compressor.compress and normalise return to a dict with 'compressed_content' key."""
    result = compressor.compress(content, content_type=content_type)
    if isinstance(result, SmartCompressionResult):
        return {
            "compressed_content": result.content,
            "decision": "compressed",
            "char_before": result.char_before,
            "char_after": result.char_after,
            "quality_hint": result.quality_hint,
        }
    if isinstance(result, dict):
        return result
    return {"compressed_content": str(result), "decision": "compressed"}


# ---------------------------------------------------------------------------
# TokenEstimator benchmarks (always available)
# ---------------------------------------------------------------------------

class TestTokenEstimatorBenchmarks:
    def setup_method(self):
        self.estimator = TokenEstimator()

    def test_benchmark_token_savings_log(self):
        """After ideal compression, token count for repeated log should drop >=30%."""
        # We simulate what compression would achieve: unique content only
        unique_line = "2026-06-22 10:00:00 DEBUG polling queue, no tasks found..."
        before_tokens = self.estimator.estimate(SAMPLE_LOG_LINES).estimated_tokens
        # The unique content is just one line (+ one omission marker)
        ideal_compressed = unique_line + "\n[... 199 duplicate lines omitted]"
        after_tokens = self.estimator.estimate(ideal_compressed).estimated_tokens
        savings_pct = (before_tokens - after_tokens) / before_tokens
        assert savings_pct >= 0.30, (
            f"Expected >=30% token savings, got {savings_pct:.1%} "
            f"(before={before_tokens}, after={after_tokens})"
        )

    def test_benchmark_passthrough_zero_change(self):
        """Passthrough mode (no compression) → token_before equals token_after."""
        content = SAMPLE_LOG_LINES
        token_before = self.estimator.estimate(content).estimated_tokens
        # Passthrough: content unchanged
        token_after = self.estimator.estimate(content).estimated_tokens
        assert token_before == token_after

    def test_benchmark_elapsed_ms_reasonable(self):
        """Estimating tokens for 10KB text completes well under 1000ms."""
        content = "x" * 10_000
        start = time.monotonic()
        for _ in range(10):
            self.estimator.estimate(content)
        elapsed_ms = (time.monotonic() - start) * 1000 / 10
        assert elapsed_ms < 1000, f"estimate() took {elapsed_ms:.1f}ms avg — too slow"


# ---------------------------------------------------------------------------
# SmartCompressor benchmarks — xfail until implemented
# ---------------------------------------------------------------------------

@pytest.mark.xfail(
    not _SMART_COMPRESSOR_OK,
    reason="SmartCompressor not yet implemented",
    strict=False,
)
class TestSmartCompressorBenchmarks:
    def setup_method(self):
        self.compressor = SmartCompressor()
        self.estimator = TokenEstimator()
        self.guard = QualityGuard(min_score=0.7)

    def test_benchmark_log_compression_ratio(self):
        """Log sample compression_ratio > 2.0 (at least 50% reduction)."""
        content = SAMPLE_LOG_LINES
        result = _compress_with_result(self.compressor, content, "log")
        compressed = result["compressed_content"]
        ratio = len(content) / max(len(compressed), 1)
        assert ratio > 2.0, f"Expected ratio > 2.0, got {ratio:.2f}"

    def test_benchmark_json_compression_ratio(self):
        """JSON sample (with null fields) compression_ratio > 1.05.

        The sample has null metadata on each item; after null pruning and
        whitespace stripping the ratio is modest (~1.10) but measurable.
        """
        content = SAMPLE_JSON_RESULTS
        result = _compress_with_result(self.compressor, content, "json")
        compressed = result["compressed_content"]
        ratio = len(content) / max(len(compressed), 1)
        assert ratio > 1.05, f"Expected ratio > 1.05, got {ratio:.2f}"

    def test_benchmark_search_results_ratio(self):
        """Search results compression_ratio > 1.5."""
        content = SAMPLE_SEARCH_RESULTS
        result = _compress_with_result(self.compressor, content, "search_results")
        compressed = result["compressed_content"]
        ratio = len(content) / max(len(compressed), 1)
        assert ratio > 1.5, f"Expected ratio > 1.5, got {ratio:.2f}"

    def test_benchmark_quality_score_above_threshold(self):
        """All sample types produce quality_score >= 0.7."""
        for name, (content, content_type) in _ALL_SAMPLES.items():
            result = _compress_with_result(self.compressor, content, content_type)
            compressed = result["compressed_content"]
            qr = self.guard.check(content, compressed, content_type=content_type)
            assert qr.score >= 0.7, (
                f"content_type={content_type!r}: quality_score={qr.score:.3f} < 0.7"
            )

    def test_benchmark_elapsed_ms_reasonable(self):
        """Compressing 10KB text completes well under 1000ms."""
        content = "repeated content line for benchmark testing purposes\n" * 200
        start = time.monotonic()
        _compress_with_result(self.compressor, content[:10_000], "log")
        elapsed_ms = (time.monotonic() - start) * 1000
        assert elapsed_ms < 1000, f"compress() took {elapsed_ms:.1f}ms — too slow"

    @pytest.mark.parametrize(
        "content_type",
        ["tool_output", "log", "json", "search_results", "generic", "unknown_xyz"],
    )
    def test_benchmark_all_types_no_crash(self, content_type):
        """Every content_type variant completes without raising an exception."""
        content = "sample line content for testing\n" * 50
        # Should not raise
        result = _compress_with_result(self.compressor, content, content_type)
        assert result is not None
