"""Versioned, deterministic benchmark report and regression gate contracts.

The module is deliberately dependency-free.  CI and hardware runners feed it
measurements; it never discovers hardware, loads a model, or performs network
I/O.  This keeps evidence validation independent from the runtime under test.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

REPORT_SCHEMA_VERSION = "ananta.voice-restricted-benchmark-report.v1"
THRESHOLD_SCHEMA_VERSION = "ananta.voice-restricted-thresholds.v1"
_SHA256_RE = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_PROFILE_IDS = frozenset({"ci-contract", "cpu", "rtx-3080", "high-end-gpu"})
_DIRECTIONS = frozenset({"maximum", "minimum"})
_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "candidate_text",
        "encryption_key",
        "password",
        "raw_audio",
        "secret",
        "token",
        "transcript",
    }
)
_SENSITIVE_SUFFIXES = (
    "_access_token",
    "_api_key",
    "_auth_token",
    "_candidate_text",
    "_encryption_key",
    "_password",
    "_raw_audio",
    "_secret",
    "_service_token",
    "_transcript",
)


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _finite_number(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"{field} must be finite")
    return normalized


def _reject_sensitive_keys(value: object, *, path: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).casefold().replace("-", "_")
            if normalized in _SENSITIVE_KEYS or normalized.endswith(_SENSITIVE_SUFFIXES):
                raise ValueError(f"benchmark evidence contains a sensitive field at {path}.{key}")
            _reject_sensitive_keys(child, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_sensitive_keys(child, path=f"{path}[{index}]")


@dataclass(frozen=True)
class ModelEvidence:
    capability: str
    engine: str
    model_id: str
    model_revision: str
    manifest_digest: str
    quantization: str
    execution_location: str

    def validate(self) -> None:
        if not all(
            value.strip()
            for value in (
                self.capability,
                self.engine,
                self.model_id,
                self.model_revision,
                self.quantization,
                self.execution_location,
            )
        ):
            raise ValueError("model evidence fields must not be empty")
        if not _SHA256_RE.fullmatch(self.manifest_digest):
            raise ValueError("model manifest digest must be SHA-256")
        if self.execution_location not in {"voice-runtime", "restricted-inference-worker"}:
            raise ValueError("model execution location is outside the production boundary")


@dataclass(frozen=True)
class ExecutionEvidence:
    git_sha: str
    engine_versions: Mapping[str, str]
    profile_id: str
    hardware: Mapping[str, str | int | float | bool | None]
    configuration: Mapping[str, Any]
    models: tuple[ModelEvidence, ...]

    def validate(self) -> None:
        if not _GIT_SHA_RE.fullmatch(self.git_sha):
            raise ValueError("git_sha must be a full 40- or 64-character hexadecimal revision")
        if self.profile_id not in _PROFILE_IDS:
            raise ValueError("unknown benchmark execution profile")
        incomplete_engine_versions = any(
            not str(key).strip() or not str(value).strip()
            for key, value in self.engine_versions.items()
        )
        if not self.engine_versions or incomplete_engine_versions:
            raise ValueError("engine versions must be complete")
        if not self.hardware or not str(self.hardware.get("cpu") or "").strip():
            raise ValueError("hardware evidence must identify the CPU")
        if not self.configuration:
            raise ValueError("effective benchmark configuration must not be empty")
        _reject_sensitive_keys(self.hardware, path="hardware")
        _reject_sensitive_keys(self.configuration, path="configuration")
        if not self.models:
            raise ValueError("at least one model evidence record is required")
        for model in self.models:
            model.validate()


@dataclass(frozen=True)
class MetricThreshold:
    metric: str
    direction: str
    limit: float
    allowed_regression: float = 0.0
    required: bool = True

    def validate(self) -> None:
        if not self.metric.strip() or self.direction not in _DIRECTIONS:
            raise ValueError("metric threshold identity is invalid")
        _finite_number(self.limit, field=f"threshold {self.metric}.limit")
        regression = _finite_number(
            self.allowed_regression,
            field=f"threshold {self.metric}.allowed_regression",
        )
        if regression < 0:
            raise ValueError("allowed regression must not be negative")


@dataclass(frozen=True)
class ThresholdSet:
    threshold_version: str
    suite_id: str
    thresholds: tuple[MetricThreshold, ...]

    def validate(self) -> None:
        if not self.threshold_version.strip() or not self.suite_id.strip() or not self.thresholds:
            raise ValueError("threshold set identity must be complete")
        names = [item.metric for item in self.thresholds]
        if len(set(names)) != len(names):
            raise ValueError("metric thresholds must be unique")
        for threshold in self.thresholds:
            threshold.validate()

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "ThresholdSet":
        if raw.get("schema_version") != THRESHOLD_SCHEMA_VERSION:
            raise ValueError("unsupported threshold schema")
        raw_thresholds = raw.get("thresholds")
        if not isinstance(raw_thresholds, list) or not raw_thresholds or not all(
            isinstance(item, Mapping) for item in raw_thresholds
        ):
            raise ValueError("thresholds must be a non-empty list of objects")
        result = cls(
            threshold_version=str(raw.get("threshold_version") or ""),
            suite_id=str(raw.get("suite_id") or ""),
            thresholds=tuple(
                MetricThreshold(
                    metric=str(item.get("metric") or ""),
                    direction=str(item.get("direction") or ""),
                    limit=_finite_number(item.get("limit"), field="threshold limit"),
                    allowed_regression=_finite_number(
                        item.get("allowed_regression", 0.0),
                        field="allowed regression",
                    ),
                    required=bool(item.get("required", True)),
                )
                for item in raw_thresholds
            ),
        )
        result.validate()
        return result


@dataclass(frozen=True)
class GateFailure:
    metric: str
    reason_code: str
    actual: float | None
    permitted: float | None


@dataclass(frozen=True)
class BenchmarkReport:
    report_id: str
    suite_id: str
    dataset_id: str
    dataset_digest: str
    dataset_split: str
    execution: ExecutionEvidence
    threshold_version: str
    metrics: Mapping[str, float]
    baseline_metrics: Mapping[str, float]
    failures: tuple[GateFailure, ...]
    recommendation: str
    status: str
    schema_version: str = REPORT_SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def digest(self) -> str:
        return f"sha256:{hashlib.sha256(_canonical_json(self.as_dict())).hexdigest()}"


class BenchmarkReportBuilder:
    """Build a report from already measured values and a versioned policy."""

    def build(
        self,
        *,
        report_id: str,
        suite_id: str,
        dataset_id: str,
        dataset_digest: str,
        dataset_split: str,
        execution: ExecutionEvidence,
        thresholds: ThresholdSet,
        metrics: Mapping[str, object],
        baseline_metrics: Mapping[str, object] | None = None,
        promotion_subject: str = "baseline",
    ) -> BenchmarkReport:
        if not report_id.strip() or not dataset_id.strip():
            raise ValueError("report and dataset identity must be complete")
        if not _SHA256_RE.fullmatch(dataset_digest):
            raise ValueError("dataset digest must be SHA-256")
        if dataset_split not in {"ci", "hardware", "holdout"}:
            raise ValueError("dataset split is invalid")
        execution.validate()
        thresholds.validate()
        if thresholds.suite_id != suite_id:
            raise ValueError("threshold suite does not match report suite")
        normalized = {str(key): _finite_number(value, field=f"metric {key}") for key, value in metrics.items()}
        baseline = {
            str(key): _finite_number(value, field=f"baseline metric {key}")
            for key, value in (baseline_metrics or {}).items()
        }
        failures = evaluate_thresholds(normalized, baseline, thresholds.thresholds)
        if promotion_subject in {"fusion", "enhancement"} and dataset_split != "holdout":
            failures = (
                *failures,
                GateFailure(
                    metric=promotion_subject,
                    reason_code="holdout_required_for_promotion",
                    actual=None,
                    permitted=None,
                ),
            )
        status = "passed" if not failures else "failed"
        recommendation = (
            f"recommend_{promotion_subject}" if status == "passed" else f"do_not_recommend_{promotion_subject}"
        )
        return BenchmarkReport(
            report_id=report_id,
            suite_id=suite_id,
            dataset_id=dataset_id,
            dataset_digest=dataset_digest,
            dataset_split=dataset_split,
            execution=execution,
            threshold_version=thresholds.threshold_version,
            metrics=normalized,
            baseline_metrics=baseline,
            failures=tuple(failures),
            recommendation=recommendation,
            status=status,
        )


def evaluate_thresholds(
    metrics: Mapping[str, float],
    baseline: Mapping[str, float],
    thresholds: Sequence[MetricThreshold],
) -> tuple[GateFailure, ...]:
    failures: list[GateFailure] = []
    for threshold in thresholds:
        actual = metrics.get(threshold.metric)
        if actual is None:
            if threshold.required:
                failures.append(GateFailure(threshold.metric, "required_metric_missing", None, threshold.limit))
            continue
        baseline_value = baseline.get(threshold.metric)
        if threshold.direction == "maximum":
            permitted = threshold.limit
            if baseline_value is not None:
                permitted = min(permitted, baseline_value + threshold.allowed_regression)
            failed = actual > permitted and not math.isclose(actual, permitted, rel_tol=1e-12, abs_tol=1e-12)
        else:
            permitted = threshold.limit
            if baseline_value is not None:
                permitted = max(permitted, baseline_value - threshold.allowed_regression)
            failed = actual < permitted and not math.isclose(actual, permitted, rel_tol=1e-12, abs_tol=1e-12)
        if failed:
            failures.append(GateFailure(threshold.metric, "metric_regression", actual, permitted))
    return tuple(failures)
