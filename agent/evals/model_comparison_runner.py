"""Pure evaluator for paired base-versus-candidate model observations."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True, slots=True)
class ModelComparisonObservation:
    model_identity_id: str
    case_id: str
    repeat: int
    correct: bool
    refused: bool
    format_valid: bool
    hallucinated: bool
    elapsed_seconds: float


class ModelComparisonError(ValueError):
    pass


def evaluate_paired_observations(
    observations: Iterable[ModelComparisonObservation],
    *,
    expected_model_ids: tuple[str, str],
    minimum_repeats: int = 5,
) -> dict[str, object]:
    items = tuple(observations)
    if len(set(expected_model_ids)) != 2 or minimum_repeats < 1:
        raise ModelComparisonError("model_comparison_contract_invalid")
    grouped: dict[tuple[str, str], set[int]] = defaultdict(set)
    for item in items:
        if item.model_identity_id not in expected_model_ids or item.repeat < 1:
            raise ModelComparisonError("model_comparison_observation_invalid")
        key = (item.model_identity_id, item.case_id)
        if item.repeat in grouped[key]:
            raise ModelComparisonError("model_comparison_duplicate_observation")
        grouped[key].add(item.repeat)
    cases = {item.case_id for item in items}
    if not cases or any(
        grouped[(model_id, case_id)] != set(range(1, minimum_repeats + 1))
        for model_id in expected_model_ids
        for case_id in cases
    ):
        raise ModelComparisonError("model_comparison_unpaired_observations")
    metrics: dict[str, dict[str, float]] = {}
    for model_id in expected_model_ids:
        model_items = [item for item in items if item.model_identity_id == model_id]
        total = len(model_items)
        metrics[model_id] = {
            "correct_rate": sum(item.correct for item in model_items) / total,
            "refusal_rate": sum(item.refused for item in model_items) / total,
            "format_valid_rate": sum(item.format_valid for item in model_items) / total,
            "hallucination_rate": sum(item.hallucinated for item in model_items) / total,
            "mean_elapsed_seconds": sum(item.elapsed_seconds for item in model_items) / total,
        }
    return {"schema": "ananta.model-comparison-result.v1", "metrics": metrics}


__all__ = ["ModelComparisonError", "ModelComparisonObservation", "evaluate_paired_observations"]
