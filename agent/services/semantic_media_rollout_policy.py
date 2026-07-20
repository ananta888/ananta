"""Pure rollout/automatic-stop policy; ordinary calls are never a stop target."""

from __future__ import annotations

from dataclasses import dataclass

ROLLOUT_STAGES = (
    "observe_only",
    "single_pair_opt_in",
    "trusted_small_group",
    "bounded_pilot",
    "general_opt_in",
)


@dataclass(frozen=True, slots=True)
class SemanticMediaHealthSignals:
    security_findings: int = 0
    privacy_findings: int = 0
    e2ee_downgrades: int = 0
    live_p95_ratio_micros: int = 1_000_000
    live_p99_ratio_micros: int = 1_000_000
    quality_gate_passed: bool = True
    budget_ratio_micros: int = 1_000_000
    resource_ratio_micros: int = 1_000_000


@dataclass(frozen=True, slots=True)
class SemanticMediaRolloutDecision:
    semantic_action: str
    ordinary_call_action: str
    target_stage: str
    reason_codes: tuple[str, ...]


def evaluate_rollout_health(
    stage: str,
    signals: SemanticMediaHealthSignals,
) -> SemanticMediaRolloutDecision:
    if stage not in ROLLOUT_STAGES:
        raise ValueError("semantic_media_rollout_stage_invalid")
    reasons: list[str] = []
    if signals.security_findings:
        reasons.append("security_finding")
    if signals.privacy_findings:
        reasons.append("privacy_finding")
    if signals.e2ee_downgrades:
        reasons.append("e2ee_downgrade")
    if signals.live_p95_ratio_micros > 1_050_000:
        reasons.append("live_p95_slo_violation")
    if signals.live_p99_ratio_micros > 1_050_000:
        reasons.append("live_p99_slo_violation")
    if not signals.quality_gate_passed:
        reasons.append("quality_gate_failed")
    if signals.budget_ratio_micros > 1_000_000:
        reasons.append("budget_exceeded")
    if signals.resource_ratio_micros > 1_000_000:
        reasons.append("resource_limit_exceeded")
    if reasons:
        return SemanticMediaRolloutDecision("stop_and_rollback", "preserve", "observe_only", tuple(reasons))
    return SemanticMediaRolloutDecision("continue", "preserve", stage, ())


__all__ = [
    "ROLLOUT_STAGES",
    "SemanticMediaHealthSignals",
    "SemanticMediaRolloutDecision",
    "evaluate_rollout_health",
]
