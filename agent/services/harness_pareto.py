"""Pareto front and multi-objective selection (SEH-015).

No single weighted score decides a winner: candidates are kept as a Pareto front
by dominance, and an operating profile only *selects* from that front without
discarding the rest. Repeat-run confidence/variance is taken into account, so a
lucky single run does not outrank a stable one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

PROFILES = ("quality", "balanced", "cheap", "low_latency")


@dataclass(frozen=True, slots=True)
class Candidate:
    candidate_id: str
    objectives: Mapping[str, float]
    confidence: float = 1.0
    variance: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_id, str) or not self.candidate_id:
            raise ValueError("candidate_id_invalid")
        if not isinstance(self.objectives, Mapping) or not self.objectives:
            raise ValueError("candidate_objectives_invalid")
        for metric, value in self.objectives.items():
            if not isinstance(metric, str) or isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("candidate_objectives_invalid")
        if not 0.0 <= float(self.confidence) <= 1.0 or float(self.variance) < 0.0:
            raise ValueError("candidate_confidence_invalid")


class ParetoSelector:
    def __init__(self, *, maximize: Sequence[str], minimize: Sequence[str]):
        self._maximize = frozenset(maximize)
        self._minimize = frozenset(minimize)
        if not self._maximize and not self._minimize:
            raise ValueError("pareto_objectives_required")
        if self._maximize & self._minimize:
            raise ValueError("pareto_objective_direction_conflict")

    def _better_or_equal(self, metric: str, a: float, b: float) -> bool:
        if metric in self._minimize:
            return a <= b
        return a >= b

    def dominates(self, a: Candidate, b: Candidate) -> bool:
        metrics = set(self._maximize) | set(self._minimize)
        strictly_better = False
        for metric in metrics:
            av, bv = a.objectives.get(metric), b.objectives.get(metric)
            if av is None or bv is None:
                raise ValueError(f"pareto_metric_missing:{metric}")
            if not self._better_or_equal(metric, av, bv):
                return False
            if av != bv:
                strictly_better = True
        return strictly_better

    def front(self, candidates: Sequence[Candidate]) -> list[Candidate]:
        front: list[Candidate] = []
        for candidate in candidates:
            if not any(other.candidate_id != candidate.candidate_id and self.dominates(other, candidate)
                       for other in candidates):
                front.append(candidate)
        return front

    def select(self, candidates: Sequence[Candidate], *, profile: str = "balanced") -> Candidate | None:
        if profile not in PROFILES:
            raise ValueError("pareto_profile_invalid")
        front = self.front(candidates)
        if not front:
            return None
        weights = self._profile_weights(profile)
        scored = [(self._score(candidate, candidates, weights), candidate) for candidate in front]
        return max(scored, key=lambda item: (item[0], item[1].candidate_id))[1]

    def _profile_weights(self, profile: str) -> dict[str, float]:
        weights = {metric: 1.0 for metric in set(self._maximize) | set(self._minimize)}
        if profile == "quality":
            for metric in ("task_correctness", "benchmark_score", "holdout_generalization_score"):
                if metric in weights:
                    weights[metric] = 3.0
        elif profile == "cheap":
            for metric in ("total_token_cost", "token_input", "token_output"):
                if metric in weights:
                    weights[metric] = 3.0
        elif profile == "low_latency":
            for metric in ("latency_p95", "latency_p50"):
                if metric in weights:
                    weights[metric] = 3.0
        return weights

    def _score(self, candidate: Candidate, population: Sequence[Candidate], weights: Mapping[str, float]) -> float:
        score = 0.0
        for metric, weight in weights.items():
            values = [float(item.objectives[metric]) for item in population]
            low, high = min(values), max(values)
            value = float(candidate.objectives[metric])
            normalized = 0.0 if high == low else (value - low) / (high - low)
            if metric in self._minimize:
                normalized = 1.0 - normalized
            score += weight * normalized
        # A less confident / noisier candidate ranks lower at equal objectives.
        return score * float(candidate.confidence) - float(candidate.variance)
