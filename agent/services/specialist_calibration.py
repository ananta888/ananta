"""Confidence calibration and abstention evaluation (GBMF-008).

Confidence is never equated with correctness: Brier score, Expected
Calibration Error and reliability bins are measured separately. The
abstain/escalate threshold is chosen on *validation* predictions and only
then checked on the frozen holdout, so the holdout never tunes the model.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from agent.services.specialist_benchmark import Prediction

CALIBRATION_SCHEMA = "ananta.specialist-calibration.v1"
DEFAULT_BINS = 10


class SpecialistCalibrationError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def _scored(predictions: Sequence[Prediction]) -> list[tuple[float, bool]]:
    scored = []
    for item in predictions:
        if item.predicted is None:
            continue
        if item.confidence is None:
            raise SpecialistCalibrationError("specialist_calibration_confidence_missing")
        scored.append((float(item.confidence), item.predicted == item.expected))
    if not scored:
        raise SpecialistCalibrationError("specialist_calibration_empty")
    return scored


def brier_score(predictions: Sequence[Prediction]) -> float:
    scored = _scored(predictions)
    return sum((confidence - (1.0 if correct else 0.0)) ** 2 for confidence, correct in scored) / len(scored)


def reliability_bins(predictions: Sequence[Prediction], *, bins: int = DEFAULT_BINS) -> list[dict[str, Any]]:
    if type(bins) is not int or not 2 <= bins <= 100:
        raise SpecialistCalibrationError("specialist_calibration_bins_invalid")
    scored = _scored(predictions)
    buckets: list[list[tuple[float, bool]]] = [[] for _ in range(bins)]
    for confidence, correct in scored:
        index = min(bins - 1, int(confidence * bins))
        buckets[index].append((confidence, correct))
    result = []
    for index, bucket in enumerate(buckets):
        count = len(bucket)
        result.append(
            {
                "bin": index,
                "lower": index / bins,
                "upper": (index + 1) / bins,
                "count": count,
                "mean_confidence": sum(c for c, _ in bucket) / count if count else None,
                "accuracy": sum(1 for _, ok in bucket if ok) / count if count else None,
            }
        )
    return result


def expected_calibration_error(predictions: Sequence[Prediction], *, bins: int = DEFAULT_BINS) -> float:
    total = sum(1 for p in predictions if p.predicted is not None)
    error = 0.0
    for bucket in reliability_bins(predictions, bins=bins):
        if bucket["count"]:
            error += bucket["count"] / total * abs(bucket["accuracy"] - bucket["mean_confidence"])
    return error


@dataclass(frozen=True)
class AbstentionReport:
    threshold: float
    escalation_rate: float
    error_rate_below_threshold: float | None
    error_rate_above_threshold: float | None
    accepted_cases: int
    deferred_cases: int

    def to_mapping(self) -> dict[str, Any]:
        return dict(self.__dict__)


def abstention_report(predictions: Sequence[Prediction], threshold: float) -> AbstentionReport:
    """Error rates below/above the confidence threshold; deferred = escalate."""
    if not isinstance(threshold, (int, float)) or not 0.0 <= float(threshold) <= 1.0:
        raise SpecialistCalibrationError("specialist_calibration_threshold_invalid")
    scored = _scored(predictions)
    below = [correct for confidence, correct in scored if confidence < threshold]
    above = [correct for confidence, correct in scored if confidence >= threshold]
    return AbstentionReport(
        threshold=float(threshold),
        escalation_rate=len(below) / len(scored),
        error_rate_below_threshold=(sum(1 for ok in below if not ok) / len(below)) if below else None,
        error_rate_above_threshold=(sum(1 for ok in above if not ok) / len(above)) if above else None,
        accepted_cases=len(above),
        deferred_cases=len(below),
    )


def select_threshold(
    validation: Sequence[Prediction],
    *,
    max_error_above: float,
    max_escalation_rate: float = 0.5,
    candidates: Sequence[float] = tuple(i / 20 for i in range(0, 21)),
) -> float | None:
    """Smallest threshold on validation that meets the accepted-error target.

    Returns ``None`` when no candidate keeps the error above the threshold
    within ``max_error_above`` at an escalation rate below ``max_escalation_rate``.
    """
    for threshold in sorted(candidates):
        report = abstention_report(validation, threshold)
        if report.error_rate_above_threshold is None:
            continue
        if report.error_rate_above_threshold <= max_error_above and report.escalation_rate <= max_escalation_rate:
            return float(threshold)
    return None


def calibration_report(
    validation: Sequence[Prediction],
    holdout: Sequence[Prediction],
    *,
    max_error_above: float,
    bins: int = DEFAULT_BINS,
) -> dict[str, Any]:
    """Versioned calibration artifact: metrics on holdout, threshold from validation."""
    threshold = select_threshold(validation, max_error_above=max_error_above)
    holdout_abstention = abstention_report(holdout, threshold).to_mapping() if threshold is not None else None
    return {
        "schema": CALIBRATION_SCHEMA,
        "brier_score": brier_score(holdout),
        "expected_calibration_error": expected_calibration_error(holdout, bins=bins),
        "reliability_bins": reliability_bins(holdout, bins=bins),
        "threshold_source": "validation",
        "abstain_threshold": threshold,
        "holdout_abstention": holdout_abstention,
    }
