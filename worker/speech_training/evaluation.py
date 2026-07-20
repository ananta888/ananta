"""Worker-side report construction using the shared evaluation contract."""

from __future__ import annotations

from typing import Any

from ananta_contracts.speech_adaptation_evaluation import (
    EVALUATION_SCHEMA_VERSION,
    MANDATORY_PROBES,
    METRIC_NAMES,
    SpeechEvaluationError,
    validate_evaluation_report,
)
from worker.speech_training.contracts import SpeechAdaptationJob

__all__ = [
    "EVALUATION_SCHEMA_VERSION",
    "MANDATORY_PROBES",
    "METRIC_NAMES",
    "SpeechEvaluationError",
    "build_mock_evaluation",
    "validate_evaluation_report",
]


def build_mock_evaluation(job: SpeechAdaptationJob, *, force_failure: bool = False) -> dict[str, Any]:
    """Return deterministic, content-free metrics for lifecycle CI only."""

    sample_count = job.dataset.validation_sample_count
    metric_values = {
        "intelligibility": ((0.84, 0.87, 0.90, 0.94), 0.80, True),
        "lexical_fidelity": ((0.86, 0.89, 0.92, 0.96), 0.85, True),
        "timing": ((0.78, 0.82, 0.86, 0.90), 0.75, True),
        "prosody": ((0.72, 0.78, 0.83, 0.88), 0.70, True),
        "speaker_similarity": ((0.80, 0.83, 0.87, 0.91), 0.80, True),
        "hallucination_rate": ((0.04, 0.03, 0.02, 0.01), 0.05, False),
        "latency_ms": ((20.0, 28.0, 35.0, 42.0), 250.0, False),
        "cpu_seconds": ((0.2, 0.4, 0.7, 1.0), 60.0, False),
        "gpu_seconds": ((0.0, 0.0, 0.0, 0.0), 60.0, False),
        "peak_memory_bytes": (
            (32 * 1024**2, 40 * 1024**2, 48 * 1024**2, 64 * 1024**2),
            job.budget.max_ram_bytes,
            False,
        ),
    }
    metrics: dict[str, Any] = {}
    for name, (values, threshold, higher_is_better) in metric_values.items():
        variant_values = dict(zip(("generic", "local_only", "reconciled", "adapted"), values, strict=True))
        adapted = variant_values["adapted"]
        passed = adapted >= threshold if higher_is_better else adapted <= threshold
        metrics[name] = {
            "values": variant_values,
            "threshold": threshold,
            "higher_is_better": higher_is_better,
            "uncertainty": 0.01,
            "sample_count": sample_count,
            "passed": passed,
        }
    probes = {
        name: {
            "value": 0.0 if not (force_failure and name == "memorization_canary") else 1.0,
            "threshold": 0.0,
            "sample_count": max(1, min(sample_count, 8)),
            "passed": not (force_failure and name == "memorization_canary"),
        }
        for name in MANDATORY_PROBES
    }
    passed = all(item["passed"] for item in metrics.values()) and all(item["passed"] for item in probes.values())
    return {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "bindings": {
            "dataset_digest": job.dataset.dataset_digest,
            "split_digest": job.dataset.split_digest,
            "model_digest": job.base_model.model_digest,
            "config_digest": job.configuration.config_digest,
            "scope_digest": job.scope.scope_digest,
            "consent_digest": job.consent.consent_digest,
        },
        "hardware_profile": "mock-cpu-no-model",
        "sample_counts": {
            "generic": sample_count,
            "local_only": sample_count,
            "reconciled": sample_count,
            "adapted": sample_count,
            "safety_probes": sum(item["sample_count"] for item in probes.values()),
        },
        "metrics": metrics,
        "probes": probes,
        "limitations": ["deterministic lifecycle mock; no model-quality claim"],
        "passed": passed,
        "policy_version": "speech-eval-policy.v1",
    }
