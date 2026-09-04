"""Reward-provider port and deterministic built-in providers."""

from __future__ import annotations

import math
from typing import Protocol


class RewardProvider(Protocol):
    def score(self, *, prediction: str, expected: str) -> float: ...


class ExactMatchReward:
    def __init__(self, *, case_sensitive: bool = True) -> None:
        self._case_sensitive = bool(case_sensitive)

    def score(self, *, prediction: str, expected: str) -> float:
        if not self._case_sensitive:
            prediction, expected = prediction.casefold(), expected.casefold()
        return float(prediction == expected)


class NumericToleranceReward:
    def __init__(self, *, tolerance: float) -> None:
        if not math.isfinite(tolerance) or tolerance < 0:
            raise ValueError("research_reward_tolerance_invalid")
        self._tolerance = tolerance

    def score(self, *, prediction: str, expected: str) -> float:
        try:
            return float(abs(float(prediction) - float(expected)) <= self._tolerance)
        except ValueError:
            return 0.0


__all__ = ["ExactMatchReward", "NumericToleranceReward", "RewardProvider"]
