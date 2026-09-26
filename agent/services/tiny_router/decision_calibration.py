"""Confidence calibration for decision-based tool choice (JEVCPP-009).

Pure evaluation: labelled observations (expected tool, predicted tool, its
probability) in, per-threshold precision/coverage and a recommended
``min_confidence`` out. Scoring the prompts against a server is the caller's job.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

DEFAULT_THRESHOLDS = tuple(round(0.5 + step * 0.05, 2) for step in range(10)) + (0.97, 0.99)


@dataclass(frozen=True)
class Observation:
    case_id: str
    expected: str
    predicted: str
    probability: float

    @property
    def correct(self) -> bool:
        return self.predicted == self.expected


@dataclass(frozen=True)
class ThresholdRow:
    threshold: float
    accepted: int
    correct: int
    coverage: float
    precision: float
    precision_lower_bound: float

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def wilson_lower_bound(correct: int, total: int, z: float = 1.645) -> float:
    """One-sided 95 % lower bound of a success rate; 0 for no observations."""
    if total <= 0:
        return 0.0
    rate = correct / total
    denominator = 1 + z * z / total
    centre = rate + z * z / (2 * total)
    margin = z * math.sqrt(rate * (1 - rate) / total + z * z / (4 * total * total))
    return max(0.0, (centre - margin) / denominator)


def threshold_table(observations: Sequence[Observation],
                    thresholds: Iterable[float] = DEFAULT_THRESHOLDS) -> list[ThresholdRow]:
    total = len(observations)
    rows = []
    for threshold in thresholds:
        accepted = [item for item in observations if item.probability >= threshold]
        correct = sum(item.correct for item in accepted)
        rows.append(ThresholdRow(
            threshold=threshold, accepted=len(accepted), correct=correct,
            coverage=round(len(accepted) / total, 4) if total else 0.0,
            precision=round(correct / len(accepted), 4) if accepted else 1.0,
            precision_lower_bound=round(wilson_lower_bound(correct, len(accepted)), 4),
        ))
    return rows


def recommend_threshold(rows: Sequence[ThresholdRow], *, target_precision: float,
                        min_accepted: int = 10) -> float | None:
    """Lowest threshold whose accepted answers provably reach the target precision.

    The one-sided 95 % lower bound of the precision must reach the target, so
    a small, error-free sample does not count as proof; the observed precision
    of every higher threshold must reach it too, so the choice is not a lucky
    dip. ``None`` when no threshold qualifies: then the fast path keeps its
    configured threshold and stays in shadow mode until there is more data.
    """
    ordered = sorted(rows, key=lambda row: row.threshold)
    for index, row in enumerate(ordered):
        if row.accepted < min_accepted or row.precision_lower_bound < target_precision:
            continue
        higher = [item for item in ordered[index:] if item.accepted]
        if all(item.precision >= target_precision for item in higher):
            return row.threshold
    return None


def expected_calibration_error(observations: Sequence[Observation], bins: int = 5) -> float:
    """Mean gap between stated probability and observed accuracy, weighted by bin size."""
    if not observations:
        return 0.0
    error = 0.0
    for index in range(bins):
        low, high = index / bins, (index + 1) / bins
        members = [item for item in observations
                   if low <= item.probability < high or (index == bins - 1 and item.probability == 1.0)]
        if members:
            confidence = sum(item.probability for item in members) / len(members)
            accuracy = sum(item.correct for item in members) / len(members)
            error += len(members) / len(observations) * abs(confidence - accuracy)
    return round(error, 4)


def confusion(observations: Sequence[Observation]) -> dict[str, dict[str, int]]:
    matrix: dict[str, dict[str, int]] = {}
    for item in observations:
        row = matrix.setdefault(item.expected, {})
        row[item.predicted] = row.get(item.predicted, 0) + 1
    return matrix


def calibration_report(observations: Sequence[Observation], *, target_precision: float = 0.95,
                       min_accepted: int = 10) -> dict[str, Any]:
    rows = threshold_table(observations)
    total = len(observations)
    return {
        "schema": "ananta.tiny_router_decision_calibration.v1",
        "total": total,
        "accuracy": round(sum(item.correct for item in observations) / total, 4) if total else 0.0,
        "expected_calibration_error": expected_calibration_error(observations),
        "target_precision": target_precision,
        "recommended_min_confidence": recommend_threshold(
            rows, target_precision=target_precision, min_accepted=min_accepted),
        "thresholds": [row.as_dict() for row in rows],
        "confusion": confusion(observations),
        "errors": [item.__dict__ for item in observations if not item.correct],
    }
