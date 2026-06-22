"""
HCCA-004 — Compression Policy Engine

Decides whether a given CompressionRequest should be compressed, passed through,
or blocked, based on content type, task intent, sensitivity, and token budget.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.services.context_compression.adapter import CompressionRequest
    from agent.services.context_compression.token_estimator import TokenMetrics
    from agent.services.context_compression.quality_guard import QualityResult

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

NEVER_COMPRESS_TYPES: frozenset[str] = frozenset({
    "current_user_message",
    "active_patch",
    "credential",
    "secret",
    "approval_prompt",
})

PROTECT_TASK_INTENTS: frozenset[str] = frozenset({
    "debug",
    "fix",
    "review",
    "security_audit",
    "test_failure",
})

DEFAULT_COMPRESSIBLE_TYPES: frozenset[str] = frozenset({
    "tool_output",
    "json",
    "log",
    "search_results",
    "rag_results",
    "old_chat_summary",
    "codecompass_symbol_list",
})


# ---------------------------------------------------------------------------
# Policy dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CompressionPolicy:
    enabled: bool = True
    # "off" | "passthrough_with_metrics" | "compress" | "compress_aggressive"
    mode: str = "passthrough_with_metrics"
    never_compress_types: frozenset[str] = field(default_factory=lambda: NEVER_COMPRESS_TYPES)
    protect_task_intents: frozenset[str] = field(default_factory=lambda: PROTECT_TASK_INTENTS)
    compressible_types: frozenset[str] = field(default_factory=lambda: DEFAULT_COMPRESSIBLE_TYPES)
    max_input_tokens_before_considering: int = 1200
    target_reduction_percent: float = 35.0
    fallback_on_quality_risk: bool = True
    min_quality_score: float = 0.7

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls, config: dict | None = None) -> "CompressionPolicy":
        """Build a policy from a flat config dict; unknown keys are silently ignored."""
        if not config:
            return cls()
        kwargs: dict = {}
        bool_fields = {"enabled", "fallback_on_quality_risk"}
        float_fields = {"target_reduction_percent", "min_quality_score"}
        int_fields = {"max_input_tokens_before_considering"}
        frozenset_fields = {"never_compress_types", "protect_task_intents", "compressible_types"}
        for f_name in bool_fields:
            if f_name in config:
                kwargs[f_name] = bool(config[f_name])
        for f_name in float_fields:
            if f_name in config:
                kwargs[f_name] = float(config[f_name])
        for f_name in int_fields:
            if f_name in config:
                kwargs[f_name] = int(config[f_name])
        for f_name in frozenset_fields:
            if f_name in config:
                kwargs[f_name] = frozenset(config[f_name])
        if "mode" in config:
            kwargs["mode"] = str(config["mode"])
        return cls(**kwargs)

    @classmethod
    def passthrough(cls) -> "CompressionPolicy":
        """Returns a fully disabled (safe passthrough) policy."""
        return cls(enabled=False, mode="off")


# ---------------------------------------------------------------------------
# Policy Engine
# ---------------------------------------------------------------------------

class CompressionPolicyEngine:
    """Evaluate a CompressionRequest against the active CompressionPolicy."""

    def __init__(self, policy: CompressionPolicy) -> None:
        self.policy = policy

    def should_compress(
        self,
        request: "CompressionRequest",
        token_metrics: "TokenMetrics",
    ) -> tuple[bool, str]:
        """Return (should_compress, reason_code).

        reason_code examples:
        - "disabled"
        - "content_type_blocked"
        - "task_intent_protected"
        - "below_token_threshold"
        - "mode_passthrough_only"
        - "eligible"
        - "sensitivity_blocked"
        """
        policy = self.policy

        # Gate 1: globally disabled
        if not policy.enabled or policy.mode == "off":
            return False, "disabled"

        # Gate 2: content type is on the never-compress list
        if request.content_type in policy.never_compress_types:
            return False, "content_type_blocked"

        # Gate 3: sensitivity label prevents compression
        if request.sensitivity_label in ("secret",):
            return False, "sensitivity_blocked"

        # Gate 4: task intent is protected
        if request.task_intent and request.task_intent in policy.protect_task_intents:
            return False, "task_intent_protected"

        # Gate 5: content type not in the compressible allow-list
        if (
            policy.compressible_types
            and request.content_type not in policy.compressible_types
        ):
            return False, "content_type_not_compressible"

        # Gate 6: below minimum token threshold
        if token_metrics.estimated_tokens < policy.max_input_tokens_before_considering:
            return False, "below_token_threshold"

        # Gate 7: mode is passthrough-only
        if policy.mode == "passthrough_with_metrics":
            return False, "mode_passthrough_only"

        return True, "eligible"

    def check_quality_gate(
        self, quality_result: "QualityResult"
    ) -> tuple[bool, str]:
        """Return (accept_compressed, reason_code).

        reason_code examples:
        - "quality_ok"
        - "quality_below_threshold"
        - "fallback_on_risk"
        """
        policy = self.policy

        if quality_result.score >= policy.min_quality_score and quality_result.passed:
            return True, "quality_ok"

        if policy.fallback_on_quality_risk:
            return False, "fallback_on_risk"

        if quality_result.score < policy.min_quality_score:
            return False, "quality_below_threshold"

        return False, "quality_failed"
