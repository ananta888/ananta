"""Canonical approval decision for Base-vs-Adapter evaluation metrics."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class EvaluationDecision:
    score: float
    minimum_score: float
    passed: bool
    loss_improvement: float
    scorer_delta: float | None
    reason_code: str | None


def evaluate_adapter_metrics(
    metrics: Mapping[str, Any],
    *,
    minimum_score: float = 0.0,
) -> EvaluationDecision:
    """Apply one fail-closed score rule shared by store, registry and API."""

    threshold = _finite(minimum_score, "minimum evaluation score")
    if threshold < 0:
        raise ValueError("minimum evaluation score must not be negative")
    base = metrics.get("base") if isinstance(metrics.get("base"), Mapping) else {}
    adapter = metrics.get("adapter") if isinstance(metrics.get("adapter"), Mapping) else {}
    base_loss = _finite(base.get("eval_loss"), "base eval_loss")
    adapter_loss = _finite(adapter.get("eval_loss"), "adapter eval_loss")
    loss_improvement = base_loss - adapter_loss
    scorer_delta = _scorer_delta(metrics.get("wins"))
    score = min(loss_improvement, scorer_delta) if scorer_delta is not None else loss_improvement
    passed = score >= threshold
    return EvaluationDecision(
        score=score,
        minimum_score=threshold,
        passed=passed,
        loss_improvement=loss_improvement,
        scorer_delta=scorer_delta,
        reason_code=None if passed else "evaluation_score_below_threshold",
    )


def _scorer_delta(value: Any) -> float | None:
    if not isinstance(value, Mapping):
        return None
    counts: list[int] = []
    for name in ("base", "adapter", "tie"):
        raw = value.get(name, 0)
        if isinstance(raw, bool) or not isinstance(raw, int) or not 0 <= raw <= 10_000_000:
            raise ValueError(f"evaluation wins.{name} must be a bounded non-negative integer")
        counts.append(raw)
    base_wins, adapter_wins, ties = counts
    total = base_wins + adapter_wins + ties
    return (adapter_wins - base_wins) / total if total else None


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"evaluation {name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"evaluation {name} must be finite")
    return result
