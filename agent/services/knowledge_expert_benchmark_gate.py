"""Reproducible promotion gate for precomputed DMoE benchmark variants."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class KnowledgeExpertBenchmarkGate:
    def __init__(self, config: Mapping[str, Any]) -> None:
        if set(config) != {
            "schema",
            "version",
            "required_variants",
            "required_bindings",
            "promotion",
            "latency_metrics",
        } or (
            config.get("schema") != "ananta.knowledge-expert-benchmark-config.v1"
            or config.get("version") != "1.0.0"
        ):
            raise ValueError("knowledge_expert_benchmark_config_invalid")
        self._variants = tuple(str(value) for value in config.get("required_variants") or ())
        self._bindings = tuple(str(value) for value in config.get("required_bindings") or ())
        self._latency_metrics = tuple(str(value) for value in config.get("latency_metrics") or ())
        promotion = dict(config.get("promotion") or {})
        if set(promotion) != {
            "minimum_quality_delta_over_dense",
            "maximum_general_holdout_regression",
            "maximum_security_holdout_regression",
        }:
            raise ValueError("knowledge_expert_benchmark_config_invalid")
        self._minimum_delta = _bounded(promotion.get("minimum_quality_delta_over_dense"), 0.0, 1.0)
        self._general_regression = _bounded(promotion.get("maximum_general_holdout_regression"), 0.0, 1.0)
        self._security_regression = _bounded(promotion.get("maximum_security_holdout_regression"), 0.0, 1.0)
        if (
            not self._variants
            or len(set(self._variants)) != len(self._variants)
            or not self._bindings
            or len(set(self._bindings)) != len(self._bindings)
            or not self._latency_metrics
            or len(set(self._latency_metrics)) != len(self._latency_metrics)
            or "dense" not in self._variants
            or not set(self._variants).difference({"dense"})
        ):
            raise ValueError("knowledge_expert_benchmark_config_invalid")

    def evaluate(self, runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        if not isinstance(runs, Sequence) or isinstance(runs, (str, bytes)):
            return self._result(False, "benchmark_report_invalid", [])
        normalized = [dict(run) for run in runs if isinstance(run, Mapping)]
        if len(normalized) != len(runs):
            return self._result(False, "benchmark_report_invalid", [])
        names = [str(run.get("variant") or "") for run in normalized]
        if len(set(names)) != len(names):
            return self._result(False, "benchmark_variant_duplicate", sorted(_duplicates(names)))
        unexpected = sorted(set(names).difference(self._variants))
        if unexpected:
            return self._result(False, "benchmark_variants_unexpected", unexpected)
        indexed = {name: run for name, run in zip(names, normalized, strict=True)}
        missing = sorted(set(self._variants).difference(indexed))
        if missing:
            return self._result(False, "benchmark_variants_missing", missing)
        binding_values: dict[str, str] = {}
        for binding in self._bindings:
            values = {str(run.get(binding) or "") for run in indexed.values()}
            if len(values) != 1 or not _DIGEST.fullmatch(next(iter(values))):
                return self._result(False, "benchmark_binding_mismatch", [binding])
            binding_values[binding] = next(iter(values))
        score_metrics = {
            "quality_score",
            "general_holdout_score",
            "security_holdout_score",
        }
        required_metrics = score_metrics.union(self._latency_metrics)
        for variant in self._variants:
            if not required_metrics.issubset(indexed[variant]):
                return self._result(False, "benchmark_metrics_missing", [variant])
            for metric in sorted(score_metrics):
                if not _is_bounded(indexed[variant].get(metric), 0.0, 1.0):
                    return self._result(False, "benchmark_metric_invalid", [variant, metric])
            for metric in self._latency_metrics:
                if not _is_bounded(indexed[variant].get(metric), 0.0, math.inf):
                    return self._result(False, "benchmark_metric_invalid", [variant, metric])
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


def _is_bounded(value: object, minimum: float, maximum: float) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and minimum <= float(value) <= maximum
    )


def _bounded(value: object, minimum: float, maximum: float) -> float:
    if not _is_bounded(value, minimum, maximum):
        raise ValueError("knowledge_expert_benchmark_config_invalid")
    return float(value)


def _duplicates(values: Sequence[str]) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return duplicates


__all__ = ["KnowledgeExpertBenchmarkGate"]
