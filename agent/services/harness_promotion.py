"""Promotion, canary and rollback controller (SEH-018).

A candidate is only promoted after every hard gate passes and it is not
dominated by the baseline. A non-dominated candidate (trade-off, no clear win)
goes to canary instead of a full promotion, and any promoted harness can be
rolled back with a recorded reason. Promotion without a gate report is refused.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.services.harness_gates import GateReport
from agent.services.harness_pareto import Candidate, ParetoSelector

PROMOTION_ACTIONS = ("promote", "canary", "reject", "rollback")


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    candidate_id: str
    baseline_id: str
    action: str
    reason: str
    canary_fraction: float = 0.0

    def to_mapping(self) -> dict:
        return {"candidate_id": self.candidate_id, "baseline_id": self.baseline_id, "action": self.action,
                "reason": self.reason, "canary_fraction": self.canary_fraction}


@dataclass
class PromotionController:
    selector: ParetoSelector
    canary_fraction: float = 0.1

    def __post_init__(self) -> None:
        if not 0.0 < self.canary_fraction <= 1.0:
            raise ValueError("promotion_canary_fraction_invalid")

    def decide(self, *, candidate: Candidate, baseline: Candidate, gate_report: GateReport | None) -> PromotionDecision:
        if gate_report is None:
            return PromotionDecision(candidate.candidate_id, baseline.candidate_id, "reject",
                                     "promotion_requires_gate_report")
        if gate_report.disqualified:
            return PromotionDecision(candidate.candidate_id, baseline.candidate_id, "reject",
                                     f"gate_disqualified:{gate_report.reason}")
        if self.selector.dominates(candidate, baseline):
            return PromotionDecision(candidate.candidate_id, baseline.candidate_id, "promote",
                                     "dominates_baseline")
        if self.selector.dominates(baseline, candidate):
            return PromotionDecision(candidate.candidate_id, baseline.candidate_id, "reject",
                                     "dominated_by_baseline")
        return PromotionDecision(candidate.candidate_id, baseline.candidate_id, "canary", "non_dominated_tradeoff",
                                 canary_fraction=self.canary_fraction)

    @staticmethod
    def rollback(*, promoted_id: str, baseline_id: str, reason: str) -> PromotionDecision:
        if not reason:
            raise ValueError("promotion_rollback_reason_required")
        return PromotionDecision(promoted_id, baseline_id, "rollback", reason)
