"""Deterministic per-task, runtime and RL regression gates."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from ananta_contracts.research_training import canonical_digest


class ResearchTrainingQualityGate:
    def decide(
        self,
        *,
        baseline_tasks: Sequence[Mapping[str, Any]],
        candidate_tasks: Sequence[Mapping[str, Any]],
        thresholds: Mapping[str, Any],
        inference: Mapping[str, Any],
        rl_metrics: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        baseline = self._task_map(baseline_tasks)
        candidate = self._task_map(candidate_tasks)
        if set(baseline) != set(candidate):
            raise ValueError("research_quality_task_set_mismatch")
        maximum_regression = self._finite(thresholds.get("maximum_task_regression"), minimum=0)
        minimum_scores = thresholds.get("minimum_task_scores")
        if not isinstance(minimum_scores, Mapping):
            raise ValueError("research_quality_minimum_scores_invalid")
        reasons: list[str] = []
        task_deltas: dict[str, float] = {}
        for task_id in sorted(candidate):
            before = self._finite(baseline[task_id].get("score"), minimum=0, maximum=1)
            after = self._finite(candidate[task_id].get("score"), minimum=0, maximum=1)
            delta = after - before
            task_deltas[task_id] = delta
            minimum = self._finite(minimum_scores.get(task_id, 0), minimum=0, maximum=1)
            if after < minimum:
                reasons.append(f"research_task_minimum_failed:{task_id}")
            if delta < -maximum_regression:
                reasons.append(f"research_task_regression:{task_id}")
            if candidate[task_id].get("mandatory") is True and after < before:
                reasons.append(f"research_mandatory_task_regression:{task_id}")
        minimum_throughput = self._finite(thresholds.get("minimum_throughput_tokens_s"), minimum=0)
        maximum_p95 = self._finite(thresholds.get("maximum_latency_p95_ms"), minimum=0)
        maximum_memory = self._finite(thresholds.get("maximum_peak_memory_bytes"), minimum=0)
        if self._finite(inference.get("throughput_tokens_s"), minimum=0) < minimum_throughput:
            reasons.append("research_inference_throughput_gate_failed")
        if self._finite(inference.get("latency_p95_ms"), minimum=0) > maximum_p95:
            reasons.append("research_inference_latency_gate_failed")
        if self._finite(inference.get("peak_memory_bytes"), minimum=0) > maximum_memory:
            reasons.append("research_inference_memory_gate_failed")
        if rl_metrics is not None:
            if self._finite(rl_metrics.get("collapse_indicator"), minimum=0, maximum=1) > 0:
                reasons.append("research_rl_output_collapse")
            if self._finite(rl_metrics.get("reward_variance"), minimum=0) > self._finite(
                thresholds.get("maximum_reward_variance"), minimum=0
            ):
                reasons.append("research_rl_reward_variance_exceeded")
            if self._finite(rl_metrics.get("unique_rollout_rate"), minimum=0, maximum=1) < self._finite(
                thresholds.get("minimum_unique_rollout_rate"), minimum=0, maximum=1
            ):
                reasons.append("research_rl_degenerate_outputs")
        result = {
            "schema": "ananta.research-training-quality-decision.v1",
            "eligible": not reasons,
            "reason_codes": sorted(reasons),
            "task_deltas": task_deltas,
            "task_count": len(candidate),
            "critical_regressions_hidden": False,
            "human_intervention_required": False,
        }
        result["decision_digest"] = canonical_digest(result)
        return result

    @staticmethod
    def _task_map(values: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
        result: dict[str, Mapping[str, Any]] = {}
        for value in values:
            task_id = str(value.get("task_id") or "")
            if (
                not task_id
                or task_id in result
                or value.get("schema") != "ananta.research-training-versioned-task-result.v1"
                or not value.get("task_digest")
                or not value.get("dataset_digest")
                or not value.get("source_refs")
            ):
                raise ValueError("research_quality_task_result_invalid")
            result[task_id] = value
        if not result:
            raise ValueError("research_quality_task_results_empty")
        return result

    @staticmethod
    def _finite(value: object, *, minimum: float, maximum: float = float("inf")) -> float:
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            or not minimum <= float(value) <= maximum
        ):
            raise ValueError("research_quality_metric_invalid")
        return float(value)


__all__ = ["ResearchTrainingQualityGate"]
