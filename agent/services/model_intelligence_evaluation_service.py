"""Versioned, reproducible evaluation runs and compatible comparisons."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import statistics
from types import MappingProxyType
from typing import Mapping, Sequence


class ModelEvaluationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MetricDefinition:
    name: str
    unit: str
    higher_is_better: bool
    absolute_tolerance: float
    regression_threshold: float

    def __post_init__(self) -> None:
        if not self.name or not self.unit:
            raise ValueError("metric name and unit are required")
        if (
            not math.isfinite(self.absolute_tolerance)
            or self.absolute_tolerance < 0
            or not math.isfinite(self.regression_threshold)
            or self.regression_threshold < 0
        ):
            raise ValueError("metric tolerances must be finite and non-negative")

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "unit": self.unit,
            "higher_is_better": self.higher_is_better,
            "absolute_tolerance": self.absolute_tolerance,
            "regression_threshold": self.regression_threshold,
        }


@dataclass(frozen=True)
class EvaluationProfile:
    profile_id: str
    version: str
    seed: int
    prompt_digest: str
    repetitions: int
    warmups: int
    comparison_dimension: str
    require_same_runtime: bool
    metrics: tuple[MetricDefinition, ...]

    def __post_init__(self) -> None:
        if not self.profile_id or not self.version:
            raise ValueError("profile ID and version are required")
        if len(self.prompt_digest) != 64:
            raise ValueError("prompt_digest must be a SHA-256 digest")
        if self.repetitions <= 0 or self.warmups < 0:
            raise ValueError("repetitions must be positive and warmups non-negative")
        if self.comparison_dimension not in {"quality", "performance"}:
            raise ValueError("comparison_dimension is unsupported")
        names = [metric.name for metric in self.metrics]
        if not names or len(names) != len(set(names)):
            raise ValueError("profile metrics must be non-empty and unique")

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "profile_id": self.profile_id,
            "version": self.version,
            "seed": self.seed,
            "prompt_digest": self.prompt_digest,
            "repetitions": self.repetitions,
            "warmups": self.warmups,
            "comparison_dimension": self.comparison_dimension,
            "require_same_runtime": self.require_same_runtime,
            "metrics": [metric.to_dict() for metric in self.metrics],
        }


@dataclass(frozen=True)
class RuntimeIdentity:
    provider: str
    version: str
    backend: str
    configuration_digest: str

    def to_dict(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "version": self.version,
            "backend": self.backend,
            "configuration_digest": self.configuration_digest,
        }

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())


@dataclass(frozen=True)
class HardwareIdentity:
    profile_id: str
    cpu: str
    accelerator: str | None
    memory_bytes: int

    def __post_init__(self) -> None:
        if not self.profile_id or not self.cpu or self.memory_bytes <= 0:
            raise ValueError("valid hardware identity fields are required")

    def to_dict(self) -> dict[str, object]:
        return {
            "profile_id": self.profile_id,
            "cpu": self.cpu,
            "accelerator": self.accelerator,
            "memory_bytes": self.memory_bytes,
        }

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())


@dataclass(frozen=True)
class EvaluationRun:
    schema_version: str
    run_id: str
    model_id: str
    artifact_digest: str
    profile: EvaluationProfile
    runtime: RuntimeIdentity
    hardware: HardwareIdentity
    metrics: Mapping[str, float]
    prediction_artifact_digest: str | None = None

    def __post_init__(self) -> None:
        values = dict(self.metrics)
        expected = {metric.name for metric in self.profile.metrics}
        if set(values) != expected:
            raise ModelEvaluationError(
                "evaluation_metric_set_mismatch",
                "Run metrics do not match the evaluation profile.",
            )
        if any(not math.isfinite(value) for value in values.values()):
            raise ModelEvaluationError(
                "evaluation_metric_non_finite",
                "Run metrics must be finite.",
            )
        object.__setattr__(self, "metrics", MappingProxyType(values))

    def to_dict(self) -> dict[str, object]:
        body: dict[str, object] = {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "model_id": self.model_id,
            "artifact_digest": self.artifact_digest,
            "profile": self.profile.to_dict(),
            "profile_digest": self.profile.digest,
            "runtime": self.runtime.to_dict(),
            "hardware": self.hardware.to_dict(),
            "metrics": dict(sorted(self.metrics.items())),
            "prediction_artifact_digest": self.prediction_artifact_digest,
        }
        body["content_digest"] = _digest(body)
        return body


@dataclass(frozen=True)
class MetricComparison:
    name: str
    baseline: float
    candidate: float
    delta: float
    within_tolerance: bool
    regression: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "baseline": self.baseline,
            "candidate": self.candidate,
            "delta": self.delta,
            "within_tolerance": self.within_tolerance,
            "regression": self.regression,
        }


@dataclass(frozen=True)
class EvaluationComparison:
    schema_version: str
    status: str
    baseline_run_id: str
    candidate_run_id: str
    reason_code: str | None
    metrics: tuple[MetricComparison, ...]

    def to_dict(self) -> dict[str, object]:
        body: dict[str, object] = {
            "schema_version": self.schema_version,
            "status": self.status,
            "baseline_run_id": self.baseline_run_id,
            "candidate_run_id": self.candidate_run_id,
            "reason_code": self.reason_code,
            "metrics": [metric.to_dict() for metric in self.metrics],
        }
        body["content_digest"] = _digest(body)
        return body


class ModelEvaluationComparisonService:
    def compare(
        self,
        *,
        baseline: EvaluationRun,
        candidate: EvaluationRun,
    ) -> EvaluationComparison:
        reason_code = self._incompatibility_reason(baseline, candidate)
        if reason_code is not None:
            return EvaluationComparison(
                schema_version="evaluation_comparison.v1",
                status="incomparable",
                baseline_run_id=baseline.run_id,
                candidate_run_id=candidate.run_id,
                reason_code=reason_code,
                metrics=(),
            )
        comparisons: list[MetricComparison] = []
        for definition in baseline.profile.metrics:
            baseline_value = baseline.metrics[definition.name]
            candidate_value = candidate.metrics[definition.name]
            delta = candidate_value - baseline_value
            within_tolerance = (
                abs(delta) <= definition.absolute_tolerance
            )
            directional_delta = (
                -delta if definition.higher_is_better else delta
            )
            comparisons.append(
                MetricComparison(
                    name=definition.name,
                    baseline=baseline_value,
                    candidate=candidate_value,
                    delta=delta,
                    within_tolerance=within_tolerance,
                    regression=(
                        directional_delta > definition.regression_threshold
                        and not within_tolerance
                    ),
                )
            )
        return EvaluationComparison(
            schema_version="evaluation_comparison.v1",
            status="comparable",
            baseline_run_id=baseline.run_id,
            candidate_run_id=candidate.run_id,
            reason_code=None,
            metrics=tuple(comparisons),
        )

    @staticmethod
    def _incompatibility_reason(
        baseline: EvaluationRun,
        candidate: EvaluationRun,
    ) -> str | None:
        if baseline.profile.digest != candidate.profile.digest:
            return "evaluation_profile_incompatible"
        if (
            baseline.profile.require_same_runtime
            and baseline.runtime.digest != candidate.runtime.digest
        ):
            return "evaluation_runtime_incompatible"
        if (
            baseline.profile.comparison_dimension == "performance"
            and baseline.hardware.digest != candidate.hardware.digest
        ):
            return "evaluation_hardware_incompatible"
        return None


def latency_metrics(samples_ms: Sequence[float]) -> Mapping[str, float]:
    values = sorted(float(value) for value in samples_ms)
    if not values or any(not math.isfinite(value) or value < 0 for value in values):
        raise ModelEvaluationError(
            "evaluation_latency_samples_invalid",
            "Latency samples must be finite and non-negative.",
        )
    p95_index = max(0, math.ceil(len(values) * 0.95) - 1)
    return MappingProxyType(
        {
            "latency_p50_ms": float(statistics.median(values)),
            "latency_p95_ms": values[p95_index],
        }
    )
