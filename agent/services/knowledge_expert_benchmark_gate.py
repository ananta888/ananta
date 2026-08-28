"""Reproducible promotion gate for precomputed DMoE benchmark variants."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class KnowledgeExpertBenchmarkGate:
    def __init__(self, config: Mapping[str, Any]) -> None:
        self._variants = tuple(str(value) for value in config.get("required_variants") or ())
        self._bindings = tuple(str(value) for value in config.get("required_bindings") or ())
        promotion = dict(config.get("promotion") or {})
        self._minimum_delta = float(promotion.get("minimum_quality_delta_over_dense", 0.0))
        self._general_regression = float(promotion.get("maximum_general_holdout_regression", 0.0))
        self._security_regression = float(promotion.get("maximum_security_holdout_regression", 0.0))
        if not self._variants or not self._bindings:
            raise ValueError("knowledge_expert_benchmark_config_invalid")

    def evaluate(self, runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        indexed = {str(run.get("variant") or ""): dict(run) for run in runs}
        missing = sorted(set(self._variants).difference(indexed))
        if missing:
            return self._result(False, "benchmark_variants_missing", missing)
        binding_values: dict[str, str] = {}
        for binding in self._bindings:
            values = {str(run.get(binding) or "") for run in indexed.values()}
            if len(values) != 1 or not _DIGEST.fullmatch(next(iter(values))):
                return self._result(False, "benchmark_binding_mismatch", [binding])
            binding_values[binding] = next(iter(values))
        required_metrics = {
            "quality_score",
            "general_holdout_score",
            "security_holdout_score",
            "retrieval_ms",
            "cold_load_ms",
            "warm_load_ms",
            "hot_switch_ms",
            "inference_ms",
        }
        for variant in self._variants:
            if not required_metrics.issubset(indexed[variant]):
                return self._result(False, "benchmark_metrics_missing", [variant])
        dense = indexed["dense"]
        best_expert = max(float(indexed[name]["quality_score"]) for name in self._variants if name != "dense")
        if best_expert - float(dense["quality_score"]) < self._minimum_delta:
            return self._result(False, "benchmark_quality_gain_missing", [])
        for run in indexed.values():
            if float(dense["general_holdout_score"]) - float(run["general_holdout_score"]) > self._general_regression:
                return self._result(False, "benchmark_general_regression", [str(run["variant"])])
            if (
                float(dense["security_holdout_score"]) - float(run["security_holdout_score"])
                > self._security_regression
            ):
                return self._result(False, "benchmark_security_regression", [str(run["variant"])])
        return {
            **self._result(True, "benchmark_promotion_passed", []),
            "bindings": binding_values,
        }

    @staticmethod
    def _result(passed: bool, reason: str, details: list[str]) -> dict[str, Any]:
        return {
            "schema": "ananta.knowledge-expert-benchmark-gate.v1",
            "passed": passed,
            "reason_code": reason,
            "details": details,
        }


__all__ = ["KnowledgeExpertBenchmarkGate"]
