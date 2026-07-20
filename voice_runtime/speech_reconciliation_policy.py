"""Pure quality-driven stop/extension policy for bounded slow reconciliation."""

from __future__ import annotations

import math
from dataclasses import dataclass

NORMAL_INITIAL_FACTOR = 10
ABSOLUTE_FACTOR_CAP = 100


@dataclass(frozen=True)
class SpeechReconciliationQualitySample:
    current_factor: int
    authorized_factor: int
    unresolved_high_quality_conflicts: int
    quality_score: float
    previous_quality_score: float | None
    evidence_count: int
    resource_remaining: bool
    evaluation_budget_reserved: bool
    energy_limit_reached: bool = False


@dataclass(frozen=True)
class SpeechReconciliationPolicyDecision:
    action: str
    next_factor: int
    reason_code: str
    materialize_dataset: bool
    reserve_evaluation: bool


class SpeechReconciliationPolicy:
    def initial_factor(self, *, user_limit: int, authorized_factor: int) -> int:
        if not 1 <= user_limit <= ABSOLUTE_FACTOR_CAP or not 1 <= authorized_factor <= ABSOLUTE_FACTOR_CAP:
            raise ValueError("speech_reconciliation_factor_invalid")
        return min(NORMAL_INITIAL_FACTOR, user_limit, authorized_factor)

    def decide(self, sample: SpeechReconciliationQualitySample) -> SpeechReconciliationPolicyDecision:
        self._validate(sample)
        if sample.evidence_count < 1:
            return self._stop(sample, "speech_reconciliation_evidence_insufficient")
        if sample.energy_limit_reached:
            return self._stop(sample, "speech_reconciliation_energy_limit")
        if not sample.resource_remaining:
            return self._stop(sample, "speech_reconciliation_resource_limit")
        if not sample.evaluation_budget_reserved:
            return SpeechReconciliationPolicyDecision(
                "dataset_only",
                sample.current_factor,
                "speech_reconciliation_evaluation_budget_missing",
                True,
                False,
            )
        if sample.unresolved_high_quality_conflicts < 1:
            return self._stop(sample, "speech_reconciliation_conflicts_resolved")
        if sample.previous_quality_score is None:
            return self._stop(sample, "speech_reconciliation_trend_unavailable")
        improvement = sample.quality_score - sample.previous_quality_score
        if improvement < 0:
            return self._stop(sample, "speech_reconciliation_quality_regression")
        if improvement < 0.005:
            return self._stop(sample, "speech_reconciliation_quality_plateau")
        if sample.current_factor >= sample.authorized_factor:
            return self._stop(sample, "speech_reconciliation_authorized_factor_reached")
        next_factor = min(sample.authorized_factor, max(sample.current_factor + 1, sample.current_factor * 2))
        return SpeechReconciliationPolicyDecision(
            "extend",
            next_factor,
            "speech_reconciliation_positive_trend",
            False,
            True,
        )

    @staticmethod
    def _stop(sample: SpeechReconciliationQualitySample, reason: str) -> SpeechReconciliationPolicyDecision:
        return SpeechReconciliationPolicyDecision("stop", sample.current_factor, reason, True, True)

    @staticmethod
    def _validate(sample: SpeechReconciliationQualitySample) -> None:
        if not 1 <= sample.current_factor <= sample.authorized_factor <= ABSOLUTE_FACTOR_CAP:
            raise ValueError("speech_reconciliation_factor_invalid")
        scores = [sample.quality_score]
        if sample.previous_quality_score is not None:
            scores.append(sample.previous_quality_score)
        if any(not math.isfinite(value) or not 0 <= value <= 1 for value in scores):
            raise ValueError("speech_reconciliation_quality_invalid")
        if sample.unresolved_high_quality_conflicts < 0 or sample.evidence_count < 0:
            raise ValueError("speech_reconciliation_count_invalid")


__all__ = [
    "ABSOLUTE_FACTOR_CAP",
    "NORMAL_INITIAL_FACTOR",
    "SpeechReconciliationPolicy",
    "SpeechReconciliationPolicyDecision",
    "SpeechReconciliationQualitySample",
]
