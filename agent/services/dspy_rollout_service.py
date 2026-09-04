"""Side-effect-free shadow execution and bounded rollout observations."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from typing import Any

from ananta_contracts.dspy_optimization import canonical_digest, require_id


class DspyRolloutService:
    def shadow(
        self,
        *,
        tenant_id: str,
        scope_id: str,
        input_payload: Mapping[str, Any],
        execute_candidate: Callable[[], Mapping[str, Any]],
        max_observations: int,
        observation_index: int,
    ) -> dict[str, Any]:
        require_id(tenant_id, "tenant_id")
        require_id(scope_id, "scope_id")
        if not 1 <= max_observations <= 100_000 or not 0 <= observation_index < max_observations:
            raise ValueError("dspy_shadow_budget_exceeded")
        candidate = dict(execute_candidate())
        return {
            "schema": "ananta.dspy-shadow-observation.v1",
            "input_digest": canonical_digest(input_payload),
            "candidate_digest": canonical_digest(candidate),
            "observation_index": observation_index,
            "user_result_changed": False,
            "task_state_changed": False,
            "tool_executed": False,
            "artifact_promoted": False,
        }

    def evaluate_canary(
        self,
        *,
        observations: Mapping[str, float | int],
        minimum_sample_size: int,
    ) -> dict[str, Any]:
        required = {"sample_count", "security_violations", "parse_error_rate", "cost_ratio", "latency_ratio"}
        if set(observations) != required or not 1 <= minimum_sample_size <= 100_000:
            raise ValueError("dspy_canary_observation_invalid")
        sample_count = observations["sample_count"]
        if not isinstance(sample_count, int) or isinstance(sample_count, bool) or sample_count < 0:
            raise ValueError("dspy_canary_observation_invalid")
        numeric = [float(observations[key]) for key in required - {"sample_count"}]
        if any(not math.isfinite(value) or value < 0 for value in numeric):
            raise ValueError("dspy_canary_observation_invalid")
        if sample_count < minimum_sample_size:
            return {
                "decision": "continue",
                "reason_code": "dspy_canary_minimum_sample_pending",
                "automatic_stop": False,
                "human_intervention_required": False,
            }
        checks = (
            (float(observations["security_violations"]) > 0, "security_regression"),
            (float(observations["parse_error_rate"]) > 0.02, "parse_regression"),
            (float(observations["cost_ratio"]) > 1.1, "cost_regression"),
            (float(observations["latency_ratio"]) > 1.2, "latency_regression"),
        )
        reason = next((code for failed, code in checks if failed), None)
        return {
            "decision": "stop" if reason else "continue",
            "reason_code": reason or "dspy_canary_within_thresholds",
            "automatic_stop": reason is not None,
            "human_intervention_required": False,
        }


__all__ = ["DspyRolloutService"]
