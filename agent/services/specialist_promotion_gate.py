"""Automatic promotion / no-regression gate for specialists (GBMF-009).

The decision is a pure function of stored artifacts (benchmark results,
lineage, policy), so it is reproducible from the registry alone. A candidate
is promotable only when

* the frozen holdout benchmark passed the hard minimums,
* provenance/lineage is complete (base model, adapter, dataset, job, eval),
* no safety metric regressed against the previous specialist version,
* it beats the baseline/previous version on quality or — at equal quality
  within the tolerance — is measurably smaller, faster or cheaper.

Registry approval, rollback and the human governance gate stay in the
existing ML-Intern adapter registry; this module only decides.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ananta_contracts.specialist_decision import canonical_digest
from agent.services.specialist_benchmark import BenchmarkResult
from agent.services.specialist_candidates import ArtifactLineage, QualityThresholds, lineage_complete

PROMOTION_DECISION_SCHEMA = "ananta.specialist-promotion-decision.v1"


@dataclass(frozen=True)
class SpecialistPromotionPolicy:
    thresholds: QualityThresholds
    max_expected_calibration_error: float | None = None
    max_brier_score: float | None = None
    # Quality tolerance for candidates that only improve speed/size/cost.
    quality_tolerance: float = 0.005
    # Safety metrics may never get worse than the previous version by more than this.
    safety_regression_tolerance: float = 0.0
    require_calibration_when_confident: bool = True

    @property
    def policy_digest(self) -> str:
        return canonical_digest(
            {
                "thresholds": self.thresholds.__dict__,
                "max_expected_calibration_error": self.max_expected_calibration_error,
                "max_brier_score": self.max_brier_score,
                "quality_tolerance": self.quality_tolerance,
                "safety_regression_tolerance": self.safety_regression_tolerance,
                "require_calibration_when_confident": self.require_calibration_when_confident,
            }
        )


@dataclass(frozen=True)
class PromotionDecision:
    allowed: bool
    reason_codes: tuple[str, ...]
    improvement: str  # quality | efficiency | none
    candidate_result_digest: str
    previous_result_digest: str | None
    policy_digest: str
    rollback_target: str | None
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_mapping(self) -> dict[str, Any]:
        body = {
            "schema": PROMOTION_DECISION_SCHEMA,
            "allowed": self.allowed,
            "reason_codes": list(self.reason_codes),
            "improvement": self.improvement,
            "candidate_result_digest": self.candidate_result_digest,
            "previous_result_digest": self.previous_result_digest,
            "policy_digest": self.policy_digest,
            "rollback_target": self.rollback_target,
            "details": dict(self.details),
        }
        return {**body, "decision_digest": canonical_digest(body)}


def _safety_regressions(candidate: BenchmarkResult, previous: BenchmarkResult, tolerance: float) -> list[str]:
    problems = []
    if not candidate.safety.get("gate_type"):
        return problems
    for name in ("false_allow_rate", "false_deny_rate"):
        now, before = candidate.safety.get(name), previous.safety.get(name)
        if isinstance(now, (int, float)) and isinstance(before, (int, float)) and now > before + tolerance:
            problems.append(f"{name}_regressed")
    now_invalid = candidate.classification["invalid_output_rate"]
    if now_invalid > previous.classification["invalid_output_rate"] + tolerance:
        problems.append("invalid_output_rate_regressed")
    return problems


def _quality(result: BenchmarkResult) -> float:
    return min(float(result.classification["accuracy"]), float(result.classification["macro_f1"]))


def _more_efficient(candidate: BenchmarkResult, previous: BenchmarkResult) -> list[str]:
    wins = []
    for name in ("adapter_bytes", "parameter_count", "latency_ms_p50", "train_seconds"):
        now, before = candidate.resources.get(name), previous.resources.get(name)
        if isinstance(now, (int, float)) and isinstance(before, (int, float)) and before > 0 and now < before:
            wins.append(name)
    return wins


def decide_promotion(
    *,
    candidate: BenchmarkResult,
    lineage: ArtifactLineage,
    policy: SpecialistPromotionPolicy,
    previous: BenchmarkResult | None = None,
    previous_version: str | None = None,
    baseline: BenchmarkResult | None = None,
) -> PromotionDecision:
    reasons: list[str] = []
    details: dict[str, Any] = {}
    candidate_digest = candidate.to_mapping()["result_digest"]

    # 1. Provenance must be complete and point at this benchmark.
    if not lineage_complete(lineage) or lineage.benchmark_result_digest != candidate_digest:
        reasons.append("provenance_incomplete")
    if lineage.holdout_digest != candidate.holdout_digest:
        reasons.append("holdout_mismatch")
    if lineage.unverified_exports():
        details["unverified_exports"] = lineage.unverified_exports()

    # 2. Hard minimums on the frozen holdout.
    reasons.extend(policy.thresholds.violations(candidate))

    # 3. Calibration: a well-scoring but badly calibrated model is not promoted on confidence alone.
    calibration = candidate.calibration or {}
    has_confidence = any(v is not None for v in (calibration.get("expected_calibration_error"), calibration.get("brier_score")))
    if policy.require_calibration_when_confident and candidate.safety.get("deferral_rate") and not has_confidence:
        reasons.append("calibration_missing")
    ece, brier = calibration.get("expected_calibration_error"), calibration.get("brier_score")
    if policy.max_expected_calibration_error is not None and isinstance(ece, (int, float)) and ece > policy.max_expected_calibration_error:
        reasons.append("expected_calibration_error_above_maximum")
    if policy.max_brier_score is not None and isinstance(brier, (int, float)) and brier > policy.max_brier_score:
        reasons.append("brier_score_above_maximum")

    # 4. Comparison with previous version and baseline.
    improvement = "none"
    reference = previous if previous is not None else baseline
    if reference is not None:
        if reference.holdout_digest != candidate.holdout_digest or reference.benchmark_version != candidate.benchmark_version:
            reasons.append("reference_benchmark_mismatch")
        else:
            regressions = _safety_regressions(candidate, reference, policy.safety_regression_tolerance)
            reasons.extend(regressions)
            delta = _quality(candidate) - _quality(reference)
            details["quality_delta"] = delta
            if delta > policy.quality_tolerance:
                improvement = "quality"
            elif delta >= -policy.quality_tolerance:
                wins = _more_efficient(candidate, reference)
                details["efficiency_wins"] = wins
                if wins:
                    improvement = "efficiency"
                else:
                    reasons.append("no_measurable_improvement")
            else:
                reasons.append("quality_regressed")
    else:
        improvement = "quality"  # first version: hard minimums alone decide

    allowed = not reasons
    return PromotionDecision(
        allowed=allowed,
        reason_codes=tuple(dict.fromkeys(reasons)),
        improvement=improvement if allowed else "none",
        candidate_result_digest=candidate_digest,
        previous_result_digest=previous.to_mapping()["result_digest"] if previous is not None else None,
        policy_digest=policy.policy_digest,
        rollback_target=previous_version,
        details=details,
    )
