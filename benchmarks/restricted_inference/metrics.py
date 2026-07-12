"""Deterministic classification and ranking metrics."""

from __future__ import annotations

import math
from typing import Hashable, Sequence


def classification_metrics(reference: Sequence[Hashable], predicted: Sequence[Hashable]) -> dict[str, float | str]:
    if len(reference) != len(predicted):
        raise ValueError("reference and predicted must have equal length")
    labels = sorted(set(reference) | set(predicted), key=str)
    total = max(1, len(reference))
    accuracy = sum(expected == actual for expected, actual in zip(reference, predicted, strict=True)) / total
    f1_values: list[float] = []
    for label in labels:
        pairs = tuple(zip(reference, predicted, strict=True))
        true_positive = sum(expected == label and actual == label for expected, actual in pairs)
        false_positive = sum(expected != label and actual == label for expected, actual in pairs)
        false_negative = sum(expected == label and actual != label for expected, actual in pairs)
        denominator = (2 * true_positive) + false_positive + false_negative
        f1_values.append((2 * true_positive / denominator) if denominator else 0.0)
    # Single-label micro F1 equals accuracy, but is explicit in the contract.
    return {
        "schema_version": "ananta.restricted-evaluation.v1",
        "accuracy": accuracy,
        "macro_f1": sum(f1_values) / max(1, len(f1_values)),
        "micro_f1": accuracy,
    }


def ranking_metrics(
    relevant: Sequence[set[str]], ranked: Sequence[Sequence[str]], *, cutoff: int = 10
) -> dict[str, float | str]:
    if len(relevant) != len(ranked):
        raise ValueError("relevant and ranked must have equal length")
    if cutoff < 1:
        raise ValueError("cutoff must be positive")
    reciprocal_ranks: list[float] = []
    ndcg_values: list[float] = []
    for expected, results in zip(relevant, ranked, strict=True):
        bounded = list(results[:cutoff])
        first = next((index for index, item in enumerate(bounded, start=1) if item in expected), None)
        reciprocal_ranks.append(1.0 / first if first else 0.0)
        dcg = sum((1.0 / math.log2(index + 1)) for index, item in enumerate(bounded, start=1) if item in expected)
        ideal_count = min(len(expected), cutoff)
        ideal = sum(1.0 / math.log2(index + 1) for index in range(1, ideal_count + 1))
        ndcg_values.append(dcg / ideal if ideal else 0.0)
    return {
        "schema_version": "ananta.restricted-evaluation.v1",
        "mrr": sum(reciprocal_ranks) / max(1, len(reciprocal_ranks)),
        "ndcg": sum(ndcg_values) / max(1, len(ndcg_values)),
        "cutoff": float(cutoff),
    }
