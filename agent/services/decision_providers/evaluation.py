"""Benchmark metrics for decision providers (DPRV).

Reuses the tiny-router calibration primitives (``Observation``, threshold
table with Wilson lower bounds, expected calibration error, validation/holdout
split) and adds what a provider comparison needs on top: macro-F1, Brier
score, confident mistakes, latency percentiles, tokens, cost, and a simulated
cascade (accept a stage's answer at a threshold, otherwise fall back to the
next stage) with its fallback rate.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from agent.services.tiny_router.decision_calibration import (
    Observation,
    expected_calibration_error,
    is_holdout,
    split_report,
    threshold_table,
)

THRESHOLDS = (0.70, 0.80, 0.90, 0.95, 0.98)
CONFIDENT = 0.90  # "high-confidence" for mistake counting


@dataclass(frozen=True)
class Run:
    """One provider's answer to one case."""

    case_id: str
    expected: str
    predicted: str | None  # None: error / invalid response
    confidence: float
    acceptable: tuple[str, ...] = ()
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    error: str | None = None

    @property
    def correct(self) -> bool:
        return self.predicted is not None and (self.predicted == self.expected or self.predicted in self.acceptable)

    def observation(self) -> Observation:
        return Observation(self.case_id, self.expected, self.predicted or "<error>",
                           self.confidence if self.predicted is not None else 0.0, self.acceptable)


def _percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return round(ordered[index], 1)


def macro_f1(runs: Sequence[Run]) -> float:
    """Macro-F1 over the expected labels (an acceptable alternative counts as the expected label)."""
    labels = sorted({run.expected for run in runs})
    scores = []
    for label in labels:
        predicted_as = [r for r in runs if (r.expected if r.correct else r.predicted) == label]
        truly = [r for r in runs if r.expected == label]
        tp = sum(1 for r in truly if r.correct)
        precision = tp / len(predicted_as) if predicted_as else 0.0
        recall = tp / len(truly) if truly else 0.0
        scores.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    return round(sum(scores) / len(scores), 4) if scores else 0.0


def brier(runs: Sequence[Run]) -> float:
    """Mean squared gap between the stated confidence and being right (lower is better)."""
    if not runs:
        return 0.0
    return round(sum((run.confidence - (1.0 if run.correct else 0.0)) ** 2 for run in runs) / len(runs), 4)


def provider_report(runs: Sequence[Run], *, thresholds: Iterable[float] = THRESHOLDS,
                    calibrated: bool = True) -> dict[str, Any]:
    total = len(runs)
    answered = [run for run in runs if run.predicted is not None]
    latencies = [run.latency_ms for run in runs if run.error is None]
    observations = [run.observation() for run in runs]
    confident_wrong = [run for run in answered if run.confidence >= CONFIDENT and not run.correct]
    return {
        "cases": total,
        "errors": total - len(answered),
        "error_reasons": sorted({run.error for run in runs if run.error}),
        "accuracy": round(sum(run.correct for run in runs) / total, 4) if total else 0.0,
        "macro_f1": macro_f1(runs),
        "confidence_is_calibrated_probability": calibrated,
        "ece": expected_calibration_error(observations, bins=10),
        "brier": brier(answered),
        "confident_mistakes": {"threshold": CONFIDENT, "count": len(confident_wrong),
                               "cases": [run.case_id for run in confident_wrong]},
        "latency_ms": {"p50": _percentile(latencies, 0.5), "p95": _percentile(latencies, 0.95),
                       "mean": round(statistics.fmean(latencies), 1) if latencies else 0.0},
        "tokens": {"input": sum(r.input_tokens for r in runs), "output": sum(r.output_tokens for r in runs)},
        "cost_usd": {"total": round(sum(r.cost_usd for r in runs), 6),
                     "per_1000_decisions": round(1000 * sum(r.cost_usd for r in runs) / total, 4) if total else 0.0},
        "thresholds": [row.as_dict() for row in threshold_table(observations, tuple(thresholds))],
        "split": split_report(observations, target_precision=0.95, min_accepted=10),
        "wrong": [{"case": r.case_id, "expected": r.expected, "predicted": r.predicted,
                   "confidence": round(r.confidence, 3)} for r in runs if not r.correct],
    }


@dataclass(frozen=True)
class CascadeRow:
    threshold: float
    accuracy: float
    fallback_rate: float
    confident_mistakes: int
    mean_latency_ms: float
    cost_usd: float
    holdout_accuracy: float
    extras: Mapping[str, Any] = field(default_factory=dict)


def simulate_cascade(first: Mapping[str, Run], fallback: Mapping[str, Run],
                     thresholds: Iterable[float] = THRESHOLDS) -> list[dict[str, Any]]:
    """Accept ``first`` at a threshold, else pay for and use ``fallback`` (same case ids)."""
    rows = []
    ids = sorted(first)
    for threshold in thresholds:
        chosen, latency, cost, fell_back, confident_wrong = {}, [], 0.0, 0, 0
        for case_id in ids:
            run = first[case_id]
            accepted = run.predicted is not None and run.confidence >= threshold
            total_latency, total_cost = run.latency_ms, run.cost_usd
            if not accepted:
                fell_back += 1
                other = fallback[case_id]
                total_latency += other.latency_ms
                total_cost += other.cost_usd
                run = other
            elif not run.correct:
                confident_wrong += 1
            chosen[case_id] = run
            latency.append(total_latency)
            cost += total_cost
        holdout = [chosen[i] for i in ids if is_holdout(i)]
        rows.append(CascadeRow(
            threshold=threshold,
            accuracy=round(sum(r.correct for r in chosen.values()) / len(ids), 4) if ids else 0.0,
            fallback_rate=round(fell_back / len(ids), 4) if ids else 0.0,
            confident_mistakes=confident_wrong,
            mean_latency_ms=round(statistics.fmean(latency), 1) if latency else 0.0,
            cost_usd=round(cost, 6),
            holdout_accuracy=round(sum(r.correct for r in holdout) / len(holdout), 4) if holdout else 0.0,
        ).__dict__)
    return rows
