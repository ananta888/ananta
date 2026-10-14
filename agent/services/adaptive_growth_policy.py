"""Hub-owned pure growth proposals. Workers cannot approve or schedule growth."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class GrowthSignals:
    validation_losses: tuple[float, ...]
    gradient_norm: float
    expert_utilization: tuple[float, ...]
    steps_since_change: int
    changes: int
    remaining_tokens: int
    estimated_change_tokens: int


@dataclass(frozen=True)
class AdaptiveGrowthPolicy:
    enabled: bool = False
    cooldown_steps: int = 100
    max_changes: int = 2
    plateau_delta: float = 0.005
    min_gradient_norm: float = 0.01
    max_gradient_norm: float = 100
    min_utilization: float = 0.05

    def __post_init__(self):
        if (
            type(self.enabled) is not bool
            or type(self.cooldown_steps) is not int
            or self.cooldown_steps < 1
            or type(self.max_changes) is not int
            or not 0 <= self.max_changes <= 8
            or any(
                not math.isfinite(v) or v < 0
                for v in [self.plateau_delta, self.min_gradient_norm, self.max_gradient_norm, self.min_utilization]
            )
            or self.min_gradient_norm > self.max_gradient_norm
            or self.min_utilization > 1
        ):
            raise ValueError("growth_policy_invalid")

    def propose(self, signals: GrowthSignals) -> dict[str, str | bool]:
        reason = "eligible"
        numeric = (*signals.validation_losses, signals.gradient_norm, *signals.expert_utilization)
        if not self.enabled:
            reason = "disabled"
        elif any(type(v) not in {int, float} or not math.isfinite(v) or v < 0 for v in numeric):
            reason = "invalid_signals"
        elif any(
            type(v) is not int or v < 0
            for v in [
                signals.steps_since_change,
                signals.changes,
                signals.remaining_tokens,
                signals.estimated_change_tokens,
            ]
        ):
            reason = "invalid_budget"
        elif signals.changes >= self.max_changes:
            reason = "change_limit"
        elif signals.steps_since_change < self.cooldown_steps:
            reason = "cooldown"
        elif not 0 < signals.estimated_change_tokens <= signals.remaining_tokens:
            reason = "budget_exhausted"
        elif not self.min_gradient_norm <= signals.gradient_norm <= self.max_gradient_norm:
            reason = "gradient_out_of_range"
        elif len(signals.validation_losses) < 4:
            reason = "insufficient_validation"
        elif max(signals.validation_losses[-4:]) - min(signals.validation_losses[-4:]) > self.plateau_delta:
            reason = "no_plateau"
        elif signals.expert_utilization and min(signals.expert_utilization) < self.min_utilization:
            reason = "expert_collapse"
        return {
            "eligible": reason == "eligible",
            "reason": reason,
            "action": "propose_depth_growth" if reason == "eligible" else "hold",
            "grants_permission": False,
        }


@dataclass(frozen=True)
class GrowthRegressionPolicy:
    maximum_loss_increase: float = 0.02

    def __post_init__(self):
        if not math.isfinite(self.maximum_loss_increase) or not 0 <= self.maximum_loss_increase <= 1:
            raise ValueError("growth_regression_policy_invalid")

    def assess(self, *, baseline_loss: float, candidate_loss: float, checkpoint_verified: bool):
        if any(type(v) not in {int, float} or not math.isfinite(v) or v < 0 for v in (baseline_loss, candidate_loss)):
            return {"action": "blocked", "reason": "invalid_loss", "grants_permission": False}
        if candidate_loss - baseline_loss > self.maximum_loss_increase:
            return {
                "action": "restore_parent_checkpoint" if checkpoint_verified else "blocked",
                "reason": "quality_regression" if checkpoint_verified else "checkpoint_unverified",
                "grants_permission": False,
            }
        return {"action": "retain_candidate", "reason": "within_regression_budget", "grants_permission": False}
