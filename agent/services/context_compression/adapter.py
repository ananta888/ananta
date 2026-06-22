"""
HCCA-001 / HCCA-007 glue — Context Compression Adapter

Defines the public contract types (CompressionRequest, CompressionResult) and
the main ContextCompressionAdapter that orchestrates policy, redaction,
compression, and quality-gating.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.services.context_compression.ccr_store import CCRStore
from agent.services.context_compression.policy_engine import (
    CompressionPolicy,
    CompressionPolicyEngine,
)
from agent.services.context_compression.quality_guard import QualityGuard
from agent.services.context_compression.secret_redactor import SecretRedactor, SensitivityLabel
from agent.services.context_compression.smart_compressor import SmartCompressor
from agent.services.context_compression.token_estimator import TokenEstimator

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Contract types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CompressionRequest:
    content: str
    content_type: str  # "tool_output"|"json"|"log"|"search_results"|"rag_results"|
                       # "code"|"diff"|"chat_history"|"codecompass_symbol_list"
    content_id: str = ""   # upstream ID for CCR reference
    task_intent: str = ""  # "debug"|"fix"|"review"|"security_audit"|"test_failure"|"general"
    sensitivity_label: str = ""  # "safe"|"sensitive"|"secret"|"unknown"
    token_estimate: int = 0
    budget_tokens: int = 0   # 0 = no budget constraint


@dataclass(frozen=True)
class CompressionResult:
    decision: str   # "passthrough" | "compressed" | "blocked" | "failed_open_passthrough"
    reason_code: str
    content: str    # final content to use
    ccr_ref: str    # CCR reference key (empty if not stored)
    token_before: int
    token_after: int
    token_delta: int          # negative = savings
    quality_score: float      # 0.0–1.0
    compression_ratio: float  # original_tokens / result_tokens, 1.0 = no compression
    adapter_used: str         # "passthrough" | "ananta_smart_compressor" | ...
    diagnostics: dict[str, Any]
    elapsed_ms: float


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class ContextCompressionAdapter:
    """Orchestrates the full compression pipeline for a single request."""

    def __init__(
        self,
        config: dict | None = None,
        ccr_store: CCRStore | None = None,
        policy_engine: CompressionPolicyEngine | None = None,
        secret_redactor: SecretRedactor | None = None,
        smart_compressor: SmartCompressor | None = None,
        quality_guard: QualityGuard | None = None,
    ) -> None:
        self._config = config or {}
        self._enabled: bool = self._config.get("enabled", True)

        # Sub-components — use provided instances or build defaults
        self._policy_engine = policy_engine or CompressionPolicyEngine(
            CompressionPolicy.from_config(self._config)
        )
        self._ccr_store = ccr_store  # None = CCR storage disabled
        self._secret_redactor = secret_redactor or SecretRedactor()
        self._smart_compressor = smart_compressor or SmartCompressor()
        self._quality_guard = quality_guard or QualityGuard(
            min_score=self._config.get("min_quality_score", 0.7),
            fallback_on_risk=self._config.get("fallback_on_quality_risk", True),
        )
        self._token_estimator = TokenEstimator()
        self._last_diagnostics: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compress(self, request: CompressionRequest) -> CompressionResult:
        """Run the full compression pipeline and return a CompressionResult."""
        t_start = time.monotonic()

        # --- Token estimation ---
        token_metrics = self._token_estimator.estimate(request.content)
        token_before = token_metrics.estimated_tokens

        def _passthrough(reason_code: str, adapter: str = "passthrough") -> CompressionResult:
            elapsed = (time.monotonic() - t_start) * 1000
            return CompressionResult(
                decision="passthrough",
                reason_code=reason_code,
                content=request.content,
                ccr_ref="",
                token_before=token_before,
                token_after=token_before,
                token_delta=0,
                quality_score=1.0,
                compression_ratio=1.0,
                adapter_used=adapter,
                diagnostics={"policy_mode": self._policy_engine.policy.mode},
                elapsed_ms=elapsed,
            )

        # --- Policy gate ---
        try:
            should_compress, policy_reason = self._policy_engine.should_compress(
                request, token_metrics
            )
        except Exception as exc:  # pylint: disable=broad-except
            log.warning("ContextCompressionAdapter: policy check failed: %s", exc)
            return _passthrough("policy_error_passthrough", "passthrough")

        if not should_compress:
            return _passthrough(policy_reason)

        # --- Secret redaction ---
        working_content = request.content
        redaction_reasons: list[str] = []
        redacted = False
        try:
            sensitivity = self._secret_redactor.scan(working_content)
            if sensitivity in (SensitivityLabel.SECRET, SensitivityLabel.SENSITIVE):
                working_content, redaction_reasons = self._secret_redactor.redact(working_content)
                redacted = bool(redaction_reasons)
                log.debug(
                    "ContextCompressionAdapter: redacted %d secret(s)", len(redaction_reasons)
                )
        except Exception as exc:  # pylint: disable=broad-except
            log.warning("ContextCompressionAdapter: redaction failed: %s", exc)

        # --- Smart compression ---
        try:
            smart_result = self._smart_compressor.compress(
                working_content,
                request.content_type,
                self._policy_engine.policy.target_reduction_percent,
            )
            compressed_content = smart_result.content
        except Exception as exc:  # pylint: disable=broad-except
            log.warning("ContextCompressionAdapter: smart compressor failed: %s", exc)
            return _passthrough("compression_error_passthrough", "passthrough")

        # --- Quality gate ---
        try:
            quality_result = self._quality_guard.check(
                working_content, compressed_content, request.content_type
            )
            accept, quality_reason = self._policy_engine.check_quality_gate(quality_result)
        except Exception as exc:  # pylint: disable=broad-except
            log.warning("ContextCompressionAdapter: quality check failed: %s", exc)
            return _passthrough("quality_error_passthrough", "passthrough")

        if not accept:
            log.debug(
                "ContextCompressionAdapter: quality gate rejected (%s score=%.2f)",
                quality_reason,
                quality_result.score,
            )
            return _passthrough(quality_reason)

        # --- CCR store ---
        ccr_ref = ""
        if self._ccr_store is not None:
            try:
                entry = self._ccr_store.store(
                    request.content,
                    content_type=request.content_type,
                    redacted=redacted,
                )
                ccr_ref = entry.ref
            except Exception as exc:  # pylint: disable=broad-except
                log.warning("ContextCompressionAdapter: CCR store failed: %s", exc)

        # --- Build result ---
        token_after = self._token_estimator.estimate(compressed_content).estimated_tokens
        token_delta = token_after - token_before
        compression_ratio = token_before / max(token_after, 1)
        elapsed = (time.monotonic() - t_start) * 1000

        diagnostics: dict[str, Any] = {
            "strategy_used": smart_result.strategy_used,
            "lines_removed": smart_result.lines_removed,
            "quality_checks": quality_result.checks,
            "quality_reason": quality_result.reason,
            "redaction_reasons": redaction_reasons,
            "char_before": smart_result.char_before,
            "char_after": smart_result.char_after,
            "token_metrics": {
                "line_count": token_metrics.line_count,
                "word_count": token_metrics.word_count,
                "unique_line_ratio": token_metrics.unique_line_ratio,
            },
        }
        self._last_diagnostics = diagnostics

        return CompressionResult(
            decision="compressed",
            reason_code="compression_ok",
            content=compressed_content,
            ccr_ref=ccr_ref,
            token_before=token_before,
            token_after=token_after,
            token_delta=token_delta,
            quality_score=quality_result.score,
            compression_ratio=compression_ratio,
            adapter_used="ananta_smart_compressor",
            diagnostics=diagnostics,
            elapsed_ms=elapsed,
        )

    def compress_many(
        self, requests: list[CompressionRequest]
    ) -> list[CompressionResult]:
        """Compress a batch of requests. Processes sequentially."""
        return [self.compress(req) for req in requests]

    def is_enabled(self) -> bool:
        """Return True if compression is globally enabled."""
        return self._enabled and self._policy_engine.policy.enabled

    def last_diagnostics(self) -> dict[str, Any]:
        """Return diagnostics from the most recent compress() call."""
        return dict(self._last_diagnostics)

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls, config: dict | None = None) -> "ContextCompressionAdapter":
        """Build adapter from a config dict. Returns passthrough adapter if disabled."""
        config = config or {}
        if not config.get("enabled", True):
            log.debug("ContextCompressionAdapter: disabled via config — using passthrough adapter")
            passthrough_policy = CompressionPolicyEngine(CompressionPolicy.passthrough())
            return cls(
                config=config,
                policy_engine=passthrough_policy,
            )

        ccr_store: CCRStore | None = None
        if config.get("ccr_enabled", False):
            store_path = config.get("ccr_store_path", "/tmp/ananta_ccr_store")
            ccr_store = CCRStore(
                store_path=Path(store_path),
                ttl_hours=int(config.get("ccr_ttl_hours", 72)),
            )

        return cls(config=config, ccr_store=ccr_store)


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------

def build_compression_adapter(config: dict | None = None) -> ContextCompressionAdapter:
    """Convenience factory — equivalent to ContextCompressionAdapter.from_config()."""
    return ContextCompressionAdapter.from_config(config)
