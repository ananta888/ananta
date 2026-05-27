"""Candidate scoring for auto-activation decisions (ASH-021).

Computes a reproducible score block for a candidate based on:
  - simulation_passed (bool)
  - shadow_decision_count
  - shadow_duration_seconds
  - shadow_match_rate
  - fallback_reduction_estimate
  - risk_score (0.0 = no risk, 1.0 = maximum risk)
  - activation_score (0.0 = not ready, 1.0 = perfect)

Activation thresholds (from todo):
  - simulation_passed: True
  - min_shadow_decision_count: 50
  - min_shadow_duration_seconds: 30
  - min_activation_score: 0.7
  - max_risk_score: 0.3
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# Activation thresholds
MIN_SHADOW_DECISIONS = 50
MIN_SHADOW_DURATION_S = 30.0
MIN_ACTIVATION_SCORE = 0.7
MAX_RISK_SCORE = 0.3


@dataclass
class CandidateScore:
    simulation_passed: bool
    shadow_decision_count: int
    shadow_duration_seconds: float
    shadow_match_rate: float
    fallback_reduction_estimate: float  # 0.0–1.0
    risk_score: float                   # 0.0 = safe, 1.0 = risky
    activation_score: float             # 0.0 = not ready, 1.0 = ready
    meets_thresholds: bool = False
    block_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "simulation_passed": self.simulation_passed,
            "shadow_decision_count": self.shadow_decision_count,
            "shadow_duration_seconds": round(self.shadow_duration_seconds, 2),
            "shadow_match_rate": round(self.shadow_match_rate, 4),
            "fallback_reduction_estimate": round(self.fallback_reduction_estimate, 4),
            "risk_score": round(self.risk_score, 4),
            "activation_score": round(self.activation_score, 4),
            "meets_thresholds": self.meets_thresholds,
            "block_reason": self.block_reason or None,
        }


def compute_score(
    *,
    simulation_passed: bool,
    shadow_decision_count: int,
    shadow_duration_seconds: float,
    shadow_match_rate: float,
    metrics: dict[str, Any] | None = None,
) -> CandidateScore:
    """Compute a reproducible CandidateScore from shadow + simulation data."""
    m = metrics or {}

    # fallback_reduction_estimate: how much the candidate reduces fallback events
    # Estimated from metrics if available
    trace_count = max(1, int(m.get("trace_count") or 1))
    no_trigger = int(m.get("no_trigger_match_count") or 0)
    fallback_reduction = max(0.0, 1.0 - (no_trigger / trace_count))

    # risk_score: higher for lower match rate, higher for exception rate
    base_risk = 1.0 - shadow_match_rate
    # Penalise if shadow didn't run long enough
    if shadow_decision_count < MIN_SHADOW_DECISIONS:
        base_risk = min(1.0, base_risk + 0.3)
    if shadow_duration_seconds < MIN_SHADOW_DURATION_S:
        base_risk = min(1.0, base_risk + 0.2)
    risk_score = round(min(1.0, max(0.0, base_risk)), 4)

    # activation_score: weighted combination
    sim_weight = 0.3 if simulation_passed else 0.0
    match_weight = shadow_match_rate * 0.4
    fallback_weight = fallback_reduction * 0.3
    activation_score = round(min(1.0, sim_weight + match_weight + fallback_weight), 4)

    # Check all thresholds
    block_reason = ""
    if not simulation_passed:
        block_reason = "simulation_not_passed"
    elif shadow_decision_count < MIN_SHADOW_DECISIONS:
        block_reason = f"shadow_decisions_too_few:{shadow_decision_count}<{MIN_SHADOW_DECISIONS}"
    elif shadow_duration_seconds < MIN_SHADOW_DURATION_S:
        block_reason = f"shadow_duration_too_short:{shadow_duration_seconds:.1f}s<{MIN_SHADOW_DURATION_S}s"
    elif activation_score < MIN_ACTIVATION_SCORE:
        block_reason = f"activation_score_too_low:{activation_score:.3f}<{MIN_ACTIVATION_SCORE}"
    elif risk_score > MAX_RISK_SCORE:
        block_reason = f"risk_score_too_high:{risk_score:.3f}>{MAX_RISK_SCORE}"

    meets = not bool(block_reason)

    return CandidateScore(
        simulation_passed=simulation_passed,
        shadow_decision_count=shadow_decision_count,
        shadow_duration_seconds=shadow_duration_seconds,
        shadow_match_rate=shadow_match_rate,
        fallback_reduction_estimate=fallback_reduction,
        risk_score=risk_score,
        activation_score=activation_score,
        meets_thresholds=meets,
        block_reason=block_reason,
    )
