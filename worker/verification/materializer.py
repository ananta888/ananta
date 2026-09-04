"""Concrete counterexample normalization and promotion invalidation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ananta_contracts.verification import CounterexampleV1, counterexample_candidate_digest


class JsonCounterexampleMaterializer:
    def materialize(self, raw: Mapping[str, Any], *, reproduction_command: Sequence[str]) -> dict[str, Any]:
        candidate = dict(raw)
        candidate["reproduction_command"] = list(reproduction_command)
        candidate["test_candidate_digest"] = counterexample_candidate_digest(candidate)
        return CounterexampleV1(**candidate).to_dict()

    @staticmethod
    def promotion_is_current(counterexample: Mapping[str, Any], test_candidate: Mapping[str, Any]) -> bool:
        return counterexample_candidate_digest(test_candidate) == counterexample.get("test_candidate_digest")


__all__ = ["JsonCounterexampleMaterializer"]
