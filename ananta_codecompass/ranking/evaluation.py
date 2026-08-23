"""Deterministic ranking evaluation metrics."""

from __future__ import annotations

import math
from collections.abc import Sequence


def ranking_metrics(ranked: Sequence[str], relevant: set[str], *, k: int) -> dict[str, float]:
    bounded = list(ranked[:max(1, int(k))])
    first_rank = next((index + 1 for index, path in enumerate(bounded) if path in relevant), None)
    recall = len(set(bounded) & relevant) / max(1, len(relevant))
    dcg = sum((1.0 if path in relevant else 0.0) / math.log2(index + 2) for index, path in enumerate(bounded))
    ideal = sum(1.0 / math.log2(index + 2) for index in range(min(len(relevant), len(bounded))))
    return {
        "mrr": 0.0 if first_rank is None else 1.0 / first_rank,
        f"recall@{k}": recall,
        f"ndcg@{k}": 0.0 if ideal == 0 else dcg / ideal,
    }
