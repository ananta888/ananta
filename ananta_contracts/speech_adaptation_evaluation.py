"""Shared closed evaluation report validator for speech adaptation."""

from __future__ import annotations

import math
from typing import Any, Mapping

from ananta_contracts.speech_adaptation import SpeechAdaptationJob, canonical_sha256

EVALUATION_SCHEMA_VERSION = "ananta.speech-adaptation-evaluation.v1"
METRIC_NAMES = (
    "intelligibility",
    "lexical_fidelity",
    "timing",
    "prosody",
    "speaker_similarity",
    "hallucination_rate",
    "latency_ms",
    "cpu_seconds",
    "gpu_seconds",
    "peak_memory_bytes",
)
MANDATORY_PROBES = (
    "cross_speaker",
    "non_consented_speaker",
    "train_validation_leakage",
    "memorization_canary",
    "prompt_content_extraction",
)


class SpeechEvaluationError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def validate_evaluation_report(report: Mapping[str, Any], *, expected_job: SpeechAdaptationJob | None = None) -> str:
    allowed = {
        "schema_version",
        "bindings",
        "hardware_profile",
        "sample_counts",
        "metrics",
        "probes",
        "limitations",
        "passed",
        "policy_version",
    }
    if set(report) != allowed:
        raise SpeechEvaluationError(
            "speech_evaluation_shape_invalid",
            "evaluation report has unknown or missing fields",
        )
    if report.get("schema_version") != EVALUATION_SCHEMA_VERSION:
        raise SpeechEvaluationError("speech_evaluation_version_invalid", "evaluation schema version is unsupported")
    if not isinstance(report.get("passed"), bool):
        raise SpeechEvaluationError("speech_evaluation_decision_invalid", "evaluation passed must be boolean")
    hardware = report.get("hardware_profile")
    if not isinstance(hardware, str) or not hardware.strip() or len(hardware) > 128:
        raise SpeechEvaluationError("speech_evaluation_hardware_invalid", "hardware profile is required")
    policy_version = report.get("policy_version")
    if not isinstance(policy_version, str) or not policy_version.strip() or len(policy_version) > 128:
        raise SpeechEvaluationError("speech_evaluation_policy_invalid", "policy version is required")
    bindings = report.get("bindings")
    required_bindings = {
        "dataset_digest",
        "split_digest",
        "model_digest",
        "config_digest",
        "scope_digest",
        "consent_digest",
    }
    if not isinstance(bindings, Mapping) or set(bindings) != required_bindings:
        raise SpeechEvaluationError("speech_evaluation_bindings_invalid", "evaluation bindings are incomplete")
    if expected_job is not None:
        expected = {
            "dataset_digest": expected_job.dataset.dataset_digest,
            "split_digest": expected_job.dataset.split_digest,
            "model_digest": expected_job.base_model.model_digest,
            "config_digest": expected_job.configuration.config_digest,
            "scope_digest": expected_job.scope.scope_digest,
            "consent_digest": expected_job.consent.consent_digest,
        }
        if dict(bindings) != expected:
            raise SpeechEvaluationError(
                "speech_evaluation_binding_mismatch",
                "evaluation does not match the admitted job",
            )
    counts = report.get("sample_counts")
    required_counts = {"generic", "local_only", "reconciled", "adapted", "safety_probes"}
    if not isinstance(counts, Mapping) or set(counts) != required_counts:
        raise SpeechEvaluationError("speech_evaluation_samples_invalid", "all validation groups are required")
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in counts.values()):
        raise SpeechEvaluationError("speech_evaluation_samples_invalid", "validation sample counts must be positive")
    metrics = report.get("metrics")
    if not isinstance(metrics, Mapping) or set(metrics) != set(METRIC_NAMES):
        raise SpeechEvaluationError("speech_evaluation_metrics_missing", "all speech metric groups are required")
    probes = report.get("probes")
    if not isinstance(probes, Mapping) or set(probes) != set(MANDATORY_PROBES):
        raise SpeechEvaluationError("speech_evaluation_probe_missing", "all safety probes are required")
    computed_pass = True
    for group_name, group in (("metric", metrics), ("probe", probes)):
        for name, raw in group.items():
            if not isinstance(raw, Mapping):
                raise SpeechEvaluationError("speech_evaluation_value_invalid", f"{group_name} {name} is invalid")
            required = {"value", "threshold", "sample_count", "passed"}
            if group_name == "metric":
                required = {"values", "threshold", "sample_count", "passed", "higher_is_better", "uncertainty"}
            if set(raw) != required:
                raise SpeechEvaluationError("speech_evaluation_value_invalid", f"{group_name} {name} shape is invalid")
            if group_name == "metric":
                values = raw.get("values")
                if not isinstance(values, Mapping) or set(values) != {
                    "generic",
                    "local_only",
                    "reconciled",
                    "adapted",
                }:
                    raise SpeechEvaluationError(
                        "speech_evaluation_variants_missing",
                        f"metric {name} must compare all four variants",
                    )
                if any(
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                    for value in values.values()
                ):
                    raise SpeechEvaluationError("speech_evaluation_non_finite", f"metric {name} is not finite")
            finite_fields = ("threshold", "uncertainty") if group_name == "metric" else ("value", "threshold")
            for field in finite_fields:
                value = raw.get(field)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                    raise SpeechEvaluationError("speech_evaluation_non_finite", f"{group_name} {name} is not finite")
            if isinstance(raw.get("sample_count"), bool) or not isinstance(raw.get("sample_count"), int):
                raise SpeechEvaluationError("speech_evaluation_samples_invalid", f"{group_name} {name} has no samples")
            if int(raw["sample_count"]) <= 0 or not isinstance(raw.get("passed"), bool):
                raise SpeechEvaluationError("speech_evaluation_samples_invalid", f"{group_name} {name} is incomplete")
            computed_pass = computed_pass and bool(raw["passed"])
    limitations = report.get("limitations")
    if not isinstance(limitations, list) or not limitations or any(
        not isinstance(item, str) or not item.strip() or len(item) > 512 for item in limitations
    ):
        raise SpeechEvaluationError("speech_evaluation_limitations_missing", "evaluation limitations are required")
    if bool(report["passed"]) != computed_pass:
        raise SpeechEvaluationError("speech_evaluation_decision_mismatch", "evaluation decision contradicts its gates")
    return canonical_sha256(dict(report))
