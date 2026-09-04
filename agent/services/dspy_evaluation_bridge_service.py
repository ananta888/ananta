"""Deterministic baseline/candidate gates; semantic metrics cannot override them."""

from __future__ import annotations

import math
from typing import Any, Mapping

from agent.services.dspy_evaluation_attestation_service import DspyEvaluationAttestationService
from ananta_contracts.dspy_optimization import canonical_digest, require_digest


class DspyEvaluationBridgeService:
    REQUIRED_METRICS = frozenset({"quality", "parse_rate", "policy_violations", "tokens", "cost_micros", "latency_ms"})
    COMPARABILITY_FIELDS = (
        "dataset_digest",
        "metric_set_digest",
        "provider_binding_id",
        "runtime_profile",
        "test_split_digest",
        "prompt_digest",
        "dspy_version",
        "hardware_profile",
        "cache_mode",
        "sampling_digest",
        "seed",
        "repetitions",
        "warmups",
    )

    def __init__(self, attestations: DspyEvaluationAttestationService) -> None:
        self._attestations = attestations

    def compare(self, *, baseline: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
        reasons: list[str] = []
        if any(
            baseline.get(field) is None or baseline.get(field) != candidate.get(field)
            for field in self.COMPARABILITY_FIELDS
        ):
            reasons.append("dspy_evaluation_not_comparable")
        if (
            int(baseline.get("sample_count") or 0) < 20
            or baseline.get("sample_count") != candidate.get("sample_count")
            or int(baseline.get("repetitions") or 0) < 1
            or int(baseline.get("warmups") or -1) < 0
        ):
            reasons.append("dspy_evaluation_sample_invalid")
        baseline_metrics = self._metrics(baseline.get("metrics"))
        candidate_metrics = self._metrics(candidate.get("metrics"))
        baseline_error = self._standard_error(baseline.get("quality_standard_error"))
        candidate_error = self._standard_error(candidate.get("quality_standard_error"))
        deterministic_pass = (
            candidate_metrics["parse_rate"] >= baseline_metrics["parse_rate"]
            and candidate_metrics["policy_violations"] == 0
        )
        if not deterministic_pass:
            reasons.append("dspy_deterministic_gate_failed")
        uncertainty_margin = 1.96 * math.sqrt(baseline_error**2 + candidate_error**2)
        if candidate_metrics["quality"] - baseline_metrics["quality"] < 0.02 + uncertainty_margin:
            reasons.append("dspy_quality_improvement_insufficient")
        if candidate_metrics["cost_micros"] > baseline_metrics["cost_micros"] * 1.1:
            reasons.append("dspy_cost_regression")
        if candidate_metrics["latency_ms"] > baseline_metrics["latency_ms"] * 1.2:
            reasons.append("dspy_latency_regression")
        result = {
            "comparable": "dspy_evaluation_not_comparable" not in reasons,
            "promotion_eligible": not reasons,
            "reason_codes": reasons,
            "baseline_program_digest": require_digest(baseline.get("program_digest"), "baseline_program_digest"),
            "candidate_program_digest": require_digest(candidate.get("program_digest"), "candidate_program_digest"),
            "baseline_input_digest": canonical_digest(baseline),
            "candidate_input_digest": canonical_digest(candidate),
            "dataset_digest": str(candidate.get("dataset_digest") or candidate.get("dataset_manifest_digest") or ""),
            "metric_set_digest": str(candidate.get("metric_set_digest") or ""),
            "deltas": (
                {key: candidate_metrics[key] - baseline_metrics[key] for key in sorted(self.REQUIRED_METRICS)}
                if "dspy_evaluation_not_comparable" not in reasons
                else None
            ),
            "uncertainty": {
                "confidence_level": 0.95,
                "baseline_quality_standard_error": baseline_error,
                "candidate_quality_standard_error": candidate_error,
                "quality_margin": uncertainty_margin,
            },
            "run_manifest_digest": canonical_digest(
                {field: candidate.get(field) for field in self.COMPARABILITY_FIELDS}
            ),
            "deterministic_gate_passed": deterministic_pass,
            "human_intervention_required": False,
        }
        result["evaluation_digest"] = canonical_digest(result)
        result["attestation"] = self._attestations.issue(result)
        return result

    def _metrics(self, raw: object) -> dict[str, float]:
        if not isinstance(raw, Mapping) or set(raw) != self.REQUIRED_METRICS:
            raise ValueError("dspy_metric_set_invalid")
        values = {key: float(raw[key]) for key in self.REQUIRED_METRICS}
        if any(not math.isfinite(value) or value < 0 for value in values.values()):
            raise ValueError("dspy_metric_value_invalid")
        if values["quality"] > 1 or values["parse_rate"] > 1 or not values["policy_violations"].is_integer():
            raise ValueError("dspy_metric_value_invalid")
        return values

    @staticmethod
    def _standard_error(raw: object) -> float:
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError("dspy_evaluation_uncertainty_invalid")
        value = float(raw)
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("dspy_evaluation_uncertainty_invalid")
        return value


__all__ = ["DspyEvaluationBridgeService"]
