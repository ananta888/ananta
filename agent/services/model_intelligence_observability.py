"""Content-free observability, sanitized correlation, and quota decision ports."""

from __future__ import annotations

import hashlib
import hmac
import math
import re
from dataclasses import dataclass, field
from typing import Mapping, Protocol, runtime_checkable

from ananta_contracts.model_intelligence import ArtifactRef

JOB_STATES = frozenset({"queued", "running", "succeeded", "failed", "cancelled"})
STABLE_REASON_CODES = frozenset(
    {
        "accepted",
        "artifact_quota_exceeded",
        "cancelled",
        "disk_quota_exceeded",
        "internal_error",
        "parallelism_quota_exceeded",
        "policy_denied",
        "queue_full",
        "ram_quota_exceeded",
        "timeout",
        "tenant_scope_mismatch",
        "vram_quota_exceeded",
        "worker_crashed",
    }
)
OPERATIONAL_SCENARIO_REASON_CODES = {
    "queue_overload": "queue_full",
    "disk_pressure": "disk_quota_exceeded",
    "timeout": "timeout",
    "cancellation": "cancelled",
    "worker_crash": "worker_crashed",
}
METRIC_DEFINITIONS: dict[str, tuple[str, str]] = {
    "model_intelligence_jobs_total": ("counter", "jobs"),
    "model_intelligence_job_duration_seconds": ("histogram", "seconds"),
    "model_intelligence_queue_depth": ("gauge", "jobs"),
    "model_intelligence_resource_bytes": ("gauge", "bytes"),
    "model_intelligence_artifact_bytes_total": ("counter", "bytes"),
    "model_intelligence_quota_rejections_total": ("counter", "rejections"),
}
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
_CORRELATION_RE = re.compile(r"^mi1\.[0-9a-f]{24}$")
_ALLOWED_LABELS = frozenset({"analysis_kind", "reason_code", "resource", "state"})


class ModelIntelligenceObservabilityError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def _identifier(value: str, reason_code: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_RE.fullmatch(normalized):
        raise ModelIntelligenceObservabilityError(reason_code)
    return normalized


def _correlation_digest(secret: bytes, domain: str, value: str) -> str:
    if len(secret) < 32:
        raise ModelIntelligenceObservabilityError("correlation_secret_too_short")
    digest = hmac.new(secret, f"{domain}:{value}".encode("utf-8"), hashlib.sha256).hexdigest()[:24]
    return f"mi1.{digest}"


@dataclass(frozen=True, slots=True)
class SanitizedModelIntelligenceCorrelation:
    correlation_id: str
    hub_job_scope: str
    worker_task_scope: str
    artifact_scope: str | None = None

    def __post_init__(self) -> None:
        for value in (self.correlation_id, self.hub_job_scope, self.worker_task_scope):
            if not _CORRELATION_RE.fullmatch(value):
                raise ModelIntelligenceObservabilityError("correlation_value_invalid")
        if self.artifact_scope is not None and not _CORRELATION_RE.fullmatch(self.artifact_scope):
            raise ModelIntelligenceObservabilityError("correlation_value_invalid")

    def public(self) -> dict[str, str]:
        result = {
            "correlation_id": self.correlation_id,
            "hub_job_scope": self.hub_job_scope,
            "worker_task_scope": self.worker_task_scope,
        }
        if self.artifact_scope is not None:
            result["artifact_scope"] = self.artifact_scope
        return result


@runtime_checkable
class ModelIntelligenceCorrelationPort(Protocol):
    def correlate(
        self,
        *,
        hub_job_id: str,
        worker_task_id: str,
        artifact_ref: ArtifactRef | None = None,
    ) -> SanitizedModelIntelligenceCorrelation: ...


class HmacModelIntelligenceCorrelationService:
    def __init__(self, secret: bytes) -> None:
        if len(secret) < 32:
            raise ModelIntelligenceObservabilityError("correlation_secret_too_short")
        self._secret = bytes(secret)

    def correlate(
        self,
        *,
        hub_job_id: str,
        worker_task_id: str,
        artifact_ref: ArtifactRef | None = None,
    ) -> SanitizedModelIntelligenceCorrelation:
        job = _identifier(hub_job_id, "correlation_job_id_invalid")
        task = _identifier(worker_task_id, "correlation_task_id_invalid")
        if artifact_ref is not None and not isinstance(artifact_ref, ArtifactRef):
            raise ModelIntelligenceObservabilityError("correlation_artifact_ref_invalid")
        artifact_material = (
            f"{artifact_ref.artifact_id}:{artifact_ref.sha256}" if artifact_ref is not None else "none"
        )
        return SanitizedModelIntelligenceCorrelation(
            correlation_id=_correlation_digest(
                self._secret,
                "correlation",
                f"{job}:{task}:{artifact_material}",
            ),
            hub_job_scope=_correlation_digest(self._secret, "job", job),
            worker_task_scope=_correlation_digest(self._secret, "task", task),
            artifact_scope=(
                _correlation_digest(self._secret, "artifact", artifact_material)
                if artifact_ref is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class ModelIntelligenceQuotaLimits:
    max_disk_bytes: int
    max_ram_bytes: int
    max_parallel_jobs: int
    max_artifact_bytes: int
    max_vram_bytes: int | None = None

    def __post_init__(self) -> None:
        values = (
            self.max_disk_bytes,
            self.max_ram_bytes,
            self.max_parallel_jobs,
            self.max_artifact_bytes,
        )
        if any(isinstance(value, bool) or value <= 0 for value in values):
            raise ModelIntelligenceObservabilityError("quota_limit_invalid")
        if self.max_vram_bytes is not None and (
            isinstance(self.max_vram_bytes, bool) or self.max_vram_bytes < 0
        ):
            raise ModelIntelligenceObservabilityError("quota_limit_invalid")


@dataclass(frozen=True, slots=True)
class TenantResourceSnapshot:
    tenant_id: str
    disk_bytes: int = 0
    ram_bytes: int = 0
    vram_bytes: int = 0
    active_jobs: int = 0
    artifact_bytes: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "tenant_id", _identifier(self.tenant_id, "quota_tenant_invalid"))
        values = (self.disk_bytes, self.ram_bytes, self.vram_bytes, self.active_jobs, self.artifact_bytes)
        if any(isinstance(value, bool) or value < 0 for value in values):
            raise ModelIntelligenceObservabilityError("quota_snapshot_invalid")


@dataclass(frozen=True, slots=True)
class ModelIntelligenceResourceRequest:
    tenant_id: str
    disk_bytes: int = 0
    ram_bytes: int = 0
    vram_bytes: int = 0
    parallel_jobs: int = 1
    artifact_bytes: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "tenant_id", _identifier(self.tenant_id, "quota_tenant_invalid"))
        values = (self.disk_bytes, self.ram_bytes, self.vram_bytes, self.artifact_bytes)
        if (
            any(isinstance(value, bool) or value < 0 for value in values)
            or isinstance(self.parallel_jobs, bool)
            or self.parallel_jobs <= 0
        ):
            raise ModelIntelligenceObservabilityError("quota_request_invalid")


@dataclass(frozen=True, slots=True)
class ModelIntelligenceQuotaDecision:
    allowed: bool
    reason_code: str
    dimension: str | None = None


@runtime_checkable
class ModelIntelligenceQuotaPort(Protocol):
    def decide(
        self,
        snapshot: TenantResourceSnapshot,
        request: ModelIntelligenceResourceRequest,
    ) -> ModelIntelligenceQuotaDecision: ...


class ModelIntelligenceQuotaPolicy:
    """Pure pre-execution evaluator with deterministic failure precedence."""

    def __init__(self, limits: ModelIntelligenceQuotaLimits) -> None:
        self._limits = limits

    def decide(
        self,
        snapshot: TenantResourceSnapshot,
        request: ModelIntelligenceResourceRequest,
    ) -> ModelIntelligenceQuotaDecision:
        if snapshot.tenant_id != request.tenant_id:
            return ModelIntelligenceQuotaDecision(False, "tenant_scope_mismatch", "tenant")
        checks = (
            ("disk", snapshot.disk_bytes + request.disk_bytes, self._limits.max_disk_bytes, "disk_quota_exceeded"),
            ("ram", snapshot.ram_bytes + request.ram_bytes, self._limits.max_ram_bytes, "ram_quota_exceeded"),
            (
                "parallelism",
                snapshot.active_jobs + request.parallel_jobs,
                self._limits.max_parallel_jobs,
                "parallelism_quota_exceeded",
            ),
            (
                "artifact",
                snapshot.artifact_bytes + request.artifact_bytes,
                self._limits.max_artifact_bytes,
                "artifact_quota_exceeded",
            ),
        )
        for dimension, demanded, maximum, reason in checks:
            if demanded > maximum:
                return ModelIntelligenceQuotaDecision(False, reason, dimension)
        if (
            self._limits.max_vram_bytes is not None
            and snapshot.vram_bytes + request.vram_bytes > self._limits.max_vram_bytes
        ):
            return ModelIntelligenceQuotaDecision(False, "vram_quota_exceeded", "vram")
        return ModelIntelligenceQuotaDecision(True, "accepted")


@dataclass(frozen=True, slots=True)
class ModelIntelligenceMetricPoint:
    name: str
    value: int | float
    labels: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.name not in METRIC_DEFINITIONS:
            raise ModelIntelligenceObservabilityError("metric_name_invalid")
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise ModelIntelligenceObservabilityError("metric_value_invalid")
        if not math.isfinite(float(self.value)) or self.value < 0:
            raise ModelIntelligenceObservabilityError("metric_value_invalid")
        if set(self.labels) - _ALLOWED_LABELS:
            raise ModelIntelligenceObservabilityError("metric_label_invalid")
        normalized: dict[str, str] = {}
        for raw_key, raw_value in self.labels.items():
            key, value = str(raw_key), str(raw_value)
            if not _LABEL_RE.fullmatch(value):
                raise ModelIntelligenceObservabilityError("metric_label_value_invalid")
            if key == "state" and value not in JOB_STATES:
                raise ModelIntelligenceObservabilityError("metric_state_invalid")
            if key == "reason_code" and value not in STABLE_REASON_CODES:
                raise ModelIntelligenceObservabilityError("metric_reason_code_invalid")
            normalized[key] = value
        object.__setattr__(self, "labels", dict(sorted(normalized.items())))

    @property
    def kind(self) -> str:
        return METRIC_DEFINITIONS[self.name][0]

    @property
    def unit(self) -> str:
        return METRIC_DEFINITIONS[self.name][1]


@runtime_checkable
class ModelIntelligenceMetricPort(Protocol):
    def observe(self, point: ModelIntelligenceMetricPoint) -> None: ...


@dataclass(frozen=True, slots=True)
class ModelIntelligenceOperationalEvent:
    state: str
    reason_code: str
    correlation: SanitizedModelIntelligenceCorrelation
    duration_seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.state not in JOB_STATES:
            raise ModelIntelligenceObservabilityError("operational_state_invalid")
        if self.reason_code not in STABLE_REASON_CODES:
            raise ModelIntelligenceObservabilityError("operational_reason_code_invalid")
        if not math.isfinite(self.duration_seconds) or self.duration_seconds < 0:
            raise ModelIntelligenceObservabilityError("operational_duration_invalid")

    def public(self) -> dict[str, object]:
        return {
            "schema": "ananta.model-intelligence.operational-event.v1",
            "state": self.state,
            "reason_code": self.reason_code,
            "duration_seconds": self.duration_seconds,
            "correlation": self.correlation.public(),
        }


@runtime_checkable
class ModelIntelligenceOperationalEventPort(Protocol):
    def emit(self, event: ModelIntelligenceOperationalEvent) -> None: ...


def operational_reason_code(scenario: str) -> str:
    try:
        return OPERATIONAL_SCENARIO_REASON_CODES[str(scenario)]
    except KeyError as exc:
        raise ModelIntelligenceObservabilityError("operational_scenario_invalid") from exc


__all__ = [
    "HmacModelIntelligenceCorrelationService",
    "JOB_STATES",
    "METRIC_DEFINITIONS",
    "ModelIntelligenceCorrelationPort",
    "ModelIntelligenceMetricPoint",
    "ModelIntelligenceMetricPort",
    "ModelIntelligenceObservabilityError",
    "ModelIntelligenceOperationalEvent",
    "ModelIntelligenceOperationalEventPort",
    "ModelIntelligenceQuotaDecision",
    "ModelIntelligenceQuotaLimits",
    "ModelIntelligenceQuotaPolicy",
    "ModelIntelligenceQuotaPort",
    "ModelIntelligenceResourceRequest",
    "OPERATIONAL_SCENARIO_REASON_CODES",
    "STABLE_REASON_CODES",
    "SanitizedModelIntelligenceCorrelation",
    "TenantResourceSnapshot",
    "operational_reason_code",
]
