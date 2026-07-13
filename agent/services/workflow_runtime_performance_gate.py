"""Release-blocking P95 evidence for the workflow-runtime Compose profile."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from agent.services.workflow_runtime._serialization import sha256_json
from agent.services.workflow_runtime_rollout_service import (
    WorkflowRolloutPerformanceEvidence,
    WorkflowRolloutScope,
    canonical_runtime_id,
)

WORKFLOW_RUNTIME_PERFORMANCE_SAMPLES_SCHEMA = (
    "ananta.workflow_runtime_performance_samples.v1"
)
WORKFLOW_RUNTIME_COMPOSE_PERFORMANCE_SCHEMA = (
    "ananta.workflow_runtime_compose_performance_evidence.v1"
)
COMPOSE_REFERENCE_PROFILE = "docker-compose-temporal-reference-v1"

_THRESHOLDS_MS = {
    "start": 2_000.0,
    "signal": 2_000.0,
    "event_projection": 1_000.0,
    "worker_restart_resume": 30_000.0,
}
_MINIMUM_SAMPLES = {
    "start": 10,
    "signal": 10,
    "event_projection": 10,
    # A restart observation represents the whole worker replacement cohort.
    # More samples are encouraged, but a release must include at least one
    # real hard-restart observation from the Compose gate.
    "worker_restart_resume": 1,
}


@dataclass(frozen=True)
class PerformanceMetricEvidence:
    metric: str
    samples_ms: tuple[float, ...]
    p95_ms: float
    threshold_ms: float

    @classmethod
    def build(
        cls,
        metric: str,
        samples: object,
    ) -> "PerformanceMetricEvidence":
        normalized_metric = str(metric or "").strip()
        if normalized_metric not in _THRESHOLDS_MS:
            raise ValueError("workflow_runtime_performance_metric_unknown")
        if isinstance(samples, (str, bytes)) or not isinstance(samples, (list, tuple)):
            raise ValueError("workflow_runtime_performance_samples_required")
        normalized: list[float] = []
        for raw in samples:
            try:
                value = float(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "workflow_runtime_performance_sample_invalid"
                ) from exc
            if not math.isfinite(value) or value < 0:
                raise ValueError("workflow_runtime_performance_sample_invalid")
            normalized.append(value)
        if len(normalized) < _MINIMUM_SAMPLES[normalized_metric]:
            raise ValueError(
                f"workflow_runtime_performance_sample_count_insufficient:{normalized_metric}"
            )
        p95 = nearest_rank_percentile(normalized, 95)
        return cls(
            metric=normalized_metric,
            samples_ms=tuple(normalized),
            p95_ms=p95,
            threshold_ms=_THRESHOLDS_MS[normalized_metric],
        )

    def assert_passed(self) -> None:
        if self.p95_ms >= self.threshold_ms:
            raise RuntimeError(
                f"workflow_runtime_performance_p95_exceeded:{self.metric}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "samples_ms": list(self.samples_ms),
            "sample_count": len(self.samples_ms),
            "p95_ms": self.p95_ms,
            "threshold_ms": self.threshold_ms,
            "comparison": "strictly_less_than",
            "status": "passed" if self.p95_ms < self.threshold_ms else "blocked",
        }


@dataclass(frozen=True)
class ComposeWorkflowRuntimePerformanceEvidence:
    evidence_id: str
    runtime_id: str
    source_revision: str
    generated_at: float
    metrics: tuple[PerformanceMetricEvidence, ...]
    reference_profile: str = COMPOSE_REFERENCE_PROFILE
    status: str = "passed"
    schema: str = WORKFLOW_RUNTIME_COMPOSE_PERFORMANCE_SCHEMA

    @classmethod
    def build(
        cls,
        *,
        runtime_id: str,
        source_revision: str,
        generated_at: float,
        samples: Mapping[str, object],
        reference_profile: str = COMPOSE_REFERENCE_PROFILE,
    ) -> "ComposeWorkflowRuntimePerformanceEvidence":
        normalized_runtime = canonical_runtime_id(runtime_id)
        if not normalized_runtime:
            raise ValueError("workflow_runtime_performance_runtime_required")
        if not str(source_revision or "").strip():
            raise ValueError("workflow_runtime_performance_revision_required")
        if str(reference_profile) != COMPOSE_REFERENCE_PROFILE:
            raise ValueError("workflow_runtime_performance_profile_unsupported")
        if not math.isfinite(float(generated_at)) or float(generated_at) <= 0:
            raise ValueError("workflow_runtime_performance_timestamp_invalid")
        if set(samples) != set(_THRESHOLDS_MS):
            raise ValueError("workflow_runtime_performance_metric_set_invalid")
        metrics = tuple(
            PerformanceMetricEvidence.build(metric, samples[metric])
            for metric in _THRESHOLDS_MS
        )
        for metric in metrics:
            metric.assert_passed()
        identity = _evidence_identity(
            runtime_id=normalized_runtime,
            source_revision=str(source_revision),
            generated_at=float(generated_at),
            metrics=metrics,
            reference_profile=str(reference_profile),
        )
        return cls(
            evidence_id=f"wrpe-{sha256_json(identity)}",
            runtime_id=normalized_runtime,
            source_revision=str(source_revision),
            generated_at=float(generated_at),
            metrics=metrics,
            reference_profile=str(reference_profile),
        )

    @classmethod
    def from_mapping(
        cls,
        raw: Mapping[str, Any],
    ) -> "ComposeWorkflowRuntimePerformanceEvidence":
        if str(raw.get("schema") or "") != WORKFLOW_RUNTIME_COMPOSE_PERFORMANCE_SCHEMA:
            raise ValueError("workflow_runtime_performance_schema_unsupported")
        if str(raw.get("status") or "") != "passed":
            raise RuntimeError("workflow_runtime_performance_evidence_not_passed")
        raw_metrics = raw.get("metrics")
        if not isinstance(raw_metrics, Mapping):
            raise ValueError("workflow_runtime_performance_metrics_required")
        samples = {
            str(metric): value.get("samples_ms")
            for metric, value in raw_metrics.items()
            if isinstance(value, Mapping)
        }
        evidence = cls.build(
            runtime_id=str(raw.get("runtime_id") or ""),
            source_revision=str(raw.get("source_revision") or ""),
            generated_at=float(raw.get("generated_at") or 0),
            samples=samples,
            reference_profile=str(raw.get("reference_profile") or ""),
        )
        if evidence.evidence_id != str(raw.get("evidence_id") or ""):
            raise ValueError("workflow_runtime_performance_evidence_digest_mismatch")
        reported = {
            str(metric): value
            for metric, value in raw_metrics.items()
            if isinstance(value, Mapping)
        }
        for metric in evidence.metrics:
            value = reported.get(metric.metric, {})
            try:
                reported_p95 = float(value.get("p95_ms"))
                reported_threshold = float(value.get("threshold_ms"))
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "workflow_runtime_performance_summary_invalid"
                ) from exc
            if (
                reported_p95 != metric.p95_ms
                or reported_threshold != metric.threshold_ms
                or int(value.get("sample_count") or 0) != len(metric.samples_ms)
            ):
                raise ValueError("workflow_runtime_performance_summary_mismatch")
        return evidence

    def metric(self, name: str) -> PerformanceMetricEvidence:
        for metric in self.metrics:
            if metric.metric == str(name):
                return metric
        raise KeyError(name)

    def to_rollout_evidence(self) -> WorkflowRolloutPerformanceEvidence:
        return WorkflowRolloutPerformanceEvidence(
            evidence_ref=self.evidence_id,
            runtime_id=self.runtime_id,
            start_p95_ms=self.metric("start").p95_ms,
            signal_p95_ms=self.metric("signal").p95_ms,
            event_projection_p95_ms=self.metric("event_projection").p95_ms,
            worker_restart_resume_p95_ms=self.metric(
                "worker_restart_resume"
            ).p95_ms,
            source_revision=self.source_revision,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "status": self.status,
            "evidence_id": self.evidence_id,
            "runtime_id": self.runtime_id,
            "source_revision": self.source_revision,
            "generated_at": self.generated_at,
            "reference_profile": self.reference_profile,
            "metrics": {
                metric.metric: metric.to_dict() for metric in self.metrics
            },
        }


class JsonWorkflowRolloutPerformanceEvidenceStore:
    """Read immutable Compose evidence for the production promotion service."""

    def __init__(
        self,
        path: str | Path,
        *,
        expected_source_revision: str = "",
    ) -> None:
        self._path = Path(path)
        self._expected_source_revision = str(expected_source_revision).strip()

    def get_evidence(
        self,
        *,
        scope: WorkflowRolloutScope,
        runtime_id: str,
    ) -> WorkflowRolloutPerformanceEvidence:
        scope.assert_valid()
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                "workflow_runtime_performance_evidence_unavailable"
            ) from exc
        if not isinstance(raw, Mapping):
            raise ValueError("workflow_runtime_performance_evidence_invalid")
        evidence = ComposeWorkflowRuntimePerformanceEvidence.from_mapping(raw)
        if evidence.runtime_id != canonical_runtime_id(runtime_id):
            raise RuntimeError("workflow_runtime_performance_runtime_mismatch")
        if (
            self._expected_source_revision
            and evidence.source_revision != self._expected_source_revision
        ):
            raise RuntimeError("workflow_runtime_performance_revision_mismatch")
        return evidence.to_rollout_evidence()


def nearest_rank_percentile(values: list[float], percentile: int) -> float:
    if not values:
        raise ValueError("workflow_runtime_performance_samples_required")
    if not 1 <= int(percentile) <= 100:
        raise ValueError("workflow_runtime_performance_percentile_invalid")
    ordered = sorted(float(value) for value in values)
    rank = max(1, math.ceil((int(percentile) / 100.0) * len(ordered)))
    return ordered[rank - 1]


def _evidence_identity(
    *,
    runtime_id: str,
    source_revision: str,
    generated_at: float,
    metrics: tuple[PerformanceMetricEvidence, ...],
    reference_profile: str,
) -> dict[str, Any]:
    return {
        "runtime_id": runtime_id,
        "source_revision": source_revision,
        "generated_at": generated_at,
        "reference_profile": reference_profile,
        "metrics": {metric.metric: metric.to_dict() for metric in metrics},
    }


__all__ = [
    "COMPOSE_REFERENCE_PROFILE",
    "ComposeWorkflowRuntimePerformanceEvidence",
    "JsonWorkflowRolloutPerformanceEvidenceStore",
    "PerformanceMetricEvidence",
    "WORKFLOW_RUNTIME_COMPOSE_PERFORMANCE_SCHEMA",
    "WORKFLOW_RUNTIME_PERFORMANCE_SAMPLES_SCHEMA",
    "nearest_rank_percentile",
]
