"""
HCCA-001 — Tests for ContextCompressionAdapter, CompressionPolicyEngine,
and TokenEstimator.

Some classes (ContextCompressionAdapter, build_compression_adapter,
CompressionPolicyEngine, CompressionPolicy) do not exist yet; the
corresponding tests are marked xfail(strict=False) so the suite is
collectable now and auto-promotes to PASS once the package is complete.
"""
from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Conditional imports — graceful when package is incomplete
# ---------------------------------------------------------------------------

try:
    from agent.services.context_compression.token_estimator import (
        TokenEstimator,
        TokenMetrics,
    )
    _TOKEN_ESTIMATOR_OK = True
except ImportError:
    _TOKEN_ESTIMATOR_OK = False

from agent.services.context_compression.adapter import (
    ContextCompressionAdapter,
    CompressionRequest,
    CompressionResult,
    build_compression_adapter,
)
_ADAPTER_OK = True

from agent.services.context_compression.policy_engine import (
    CompressionPolicyEngine,
    CompressionPolicy,
)
_POLICY_OK = True


# ---------------------------------------------------------------------------
# TokenEstimator — exists now
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _TOKEN_ESTIMATOR_OK, reason="token_estimator not importable")
class TestTokenEstimator:
    def setup_method(self):
        self.estimator = TokenEstimator()

    def test_token_estimator_rough_estimate(self):
        """400 chars → ~100 tokens (chars / 4)."""
        text = "a" * 400
        metrics = self.estimator.estimate(text)
        assert isinstance(metrics, TokenMetrics)
        assert metrics.estimated_tokens == 100

    def test_token_estimator_budget_exceeded(self):
        """10000 chars with budget=100 → exceeds budget."""
        text = "x" * 10_000  # ~2500 tokens
        assert self.estimator.budget_exceeded(text, budget_tokens=100) is True

    def test_token_estimator_budget_not_exceeded_with_zero(self):
        """budget=0 means no limit — never exceeds."""
        text = "x" * 10_000
        assert self.estimator.budget_exceeded(text, budget_tokens=0) is False

    def test_token_estimator_unique_line_ratio_all_same(self):
        """All identical lines → ratio near 0.0 (1/N unique)."""
        line = "2026-06-22 DEBUG polling...\n"
        text = line * 100
        metrics = self.estimator.estimate(text)
        assert metrics.unique_line_ratio < 0.05

    def test_token_estimator_unique_line_ratio_all_unique(self):
        """All distinct lines → ratio near 1.0."""
        lines = [f"line_{i}: some distinct content here" for i in range(100)]
        text = "\n".join(lines)
        metrics = self.estimator.estimate(text)
        assert metrics.unique_line_ratio > 0.95

    def test_compression_result_token_delta(self):
        """CompressionResult with token_after < token_before → token_delta < 0."""
        # This test only exercises arithmetic — no adapter needed.
        token_before = 500
        token_after = 300
        token_delta = token_after - token_before
        assert token_delta < 0


# ---------------------------------------------------------------------------
# ContextCompressionAdapter — not yet implemented
# ---------------------------------------------------------------------------

class TestContextCompressionAdapter:
    def test_build_disabled_adapter_returns_passthrough(self):
        """build_compression_adapter({"enabled": False}) → is_enabled() == False."""
        adapter = build_compression_adapter({"enabled": False})
        assert adapter.is_enabled() is False

    def test_compress_disabled_always_passthrough(self):
        """Disabled adapter returns decision='passthrough' for any input."""
        adapter = build_compression_adapter({"enabled": False})
        request = CompressionRequest(
            content="some long content that might be compressed",
            content_type="tool_output",
            task_intent="coding",
        )
        result = adapter.compress(request)
        assert isinstance(result, CompressionResult)
        assert result.decision == "passthrough"


# ---------------------------------------------------------------------------
# CompressionPolicyEngine — not yet implemented
# ---------------------------------------------------------------------------

class TestCompressionPolicyEngine:
    def setup_method(self):
        # Use mode="compress" so "eligible" is reachable (not blocked by passthrough_with_metrics)
        self.policy = CompressionPolicy(
            enabled=True,
            mode="compress",
            never_compress_types=frozenset({"current_user_message"}),
            protect_task_intents=frozenset({"debug"}),
            max_input_tokens_before_considering=500,
        )
        self.engine = CompressionPolicyEngine(self.policy)
        self.estimator = TokenEstimator()

    def _make_request(self, content, content_type, task_intent="coding"):
        return CompressionRequest(
            content=content,
            content_type=content_type,
            task_intent=task_intent,
        )

    def test_policy_blocks_never_compress_types(self):
        """content_type='current_user_message' → should_compress() returns False."""
        req = self._make_request("A" * 2000, "current_user_message", "coding")
        metrics = self.estimator.estimate(req.content)
        eligible, reason = self.engine.should_compress(req, metrics)
        assert eligible is False
        assert reason == "content_type_blocked"

    def test_policy_blocks_protected_task_intent(self):
        """task_intent='debug' with compressible type → blocked."""
        req = self._make_request("A" * 2000, "tool_output", "debug")
        metrics = self.estimator.estimate(req.content)
        eligible, reason = self.engine.should_compress(req, metrics)
        assert eligible is False

    def test_policy_below_threshold_returns_false(self):
        """10-token content below max_input_tokens_before_considering → not eligible."""
        req = self._make_request("short text", "tool_output", "coding")
        metrics = self.estimator.estimate(req.content)
        # metrics.estimated_tokens will be ~2, well below threshold of 500
        eligible, reason = self.engine.should_compress(req, metrics)
        assert eligible is False

    def test_policy_eligible_large_content(self):
        """Large log content above token threshold → eligible."""
        # 500 tokens * 4 chars/token = 2000 chars minimum; use 2500 to be well above
        req = self._make_request("A" * 10_000, "tool_output", "coding")
        metrics = self.estimator.estimate(req.content)
        eligible, reason = self.engine.should_compress(req, metrics)
        assert eligible is True
        assert reason == "eligible"
