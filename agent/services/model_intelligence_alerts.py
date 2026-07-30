"""Dependency-free, content-free alert threshold evaluation for OWMA operations."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class ModelIntelligenceAlertError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ModelIntelligenceAlertThresholds:
    resource_warning_ratio: float = 0.80
    resource_critical_ratio: float = 0.95
    failure_warning_ratio: float = 0.05
    failure_critical_ratio: float = 0.20
    cancellation_warning_ratio: float = 0.10
    cancellation_critical_ratio: float = 0.30
    crash_warning_count: int = 1
    crash_critical_count: int = 3

    def __post_init__(self) -> None:
        pairs = (
            (self.resource_warning_ratio, self.resource_critical_ratio),
            (self.failure_warning_ratio, self.failure_critical_ratio),
            (self.cancellation_warning_ratio, self.cancellation_critical_ratio),
        )
        if any(
            not math.isfinite(warning)
            or not math.isfinite(critical)
            or not 0 <= warning < critical <= 1
            for warning, critical in pairs
        ):
            raise ModelIntelligenceAlertError("alert_ratio_threshold_invalid")
        if (
            isinstance(self.crash_warning_count, bool)
            or isinstance(self.crash_critical_count, bool)
            or not 0 <= self.crash_warning_count < self.crash_critical_count
        ):
            raise ModelIntelligenceAlertError("alert_crash_threshold_invalid")


@dataclass(frozen=True, slots=True)
class ModelIntelligenceOperationalSnapshot:
    queue_depth: int
    queue_limit: int
    disk_bytes: int
    disk_limit_bytes: int
    ram_bytes: int
    ram_limit_bytes: int
    artifact_bytes: int
    artifact_limit_bytes: int
    completed_jobs: int
    failed_jobs: int
    cancelled_jobs: int
    worker_crashes: int
    vram_bytes: int = 0
    vram_limit_bytes: int = 0

    def __post_init__(self) -> None:
        values = (
            self.queue_depth,
            self.queue_limit,
            self.disk_bytes,
            self.disk_limit_bytes,
            self.ram_bytes,
            self.ram_limit_bytes,
            self.artifact_bytes,
            self.artifact_limit_bytes,
            self.completed_jobs,
            self.failed_jobs,
            self.cancelled_jobs,
            self.worker_crashes,
            self.vram_bytes,
            self.vram_limit_bytes,
        )
        if any(isinstance(value, bool) or value < 0 for value in values):
            raise ModelIntelligenceAlertError("alert_snapshot_invalid")
        if not all(
            value > 0
            for value in (
                self.queue_limit,
                self.disk_limit_bytes,
                self.ram_limit_bytes,
                self.artifact_limit_bytes,
            )
        ):
            raise ModelIntelligenceAlertError("alert_snapshot_limit_invalid")
        if self.vram_bytes and not self.vram_limit_bytes:
            raise ModelIntelligenceAlertError("alert_snapshot_vram_limit_required")
        if self.failed_jobs + self.cancelled_jobs > self.completed_jobs:
            raise ModelIntelligenceAlertError("alert_snapshot_job_counts_invalid")


@dataclass(frozen=True, slots=True)
class ModelIntelligenceAlert:
    alert_id: str
    severity: str
    reason_code: str
    observed: float
    threshold: float
    runbook_section: str

    def public(self) -> dict[str, object]:
        return {
            "schema": "ananta.model-intelligence.alert.v1",
            "alert_id": self.alert_id,
            "severity": self.severity,
            "reason_code": self.reason_code,
            "observed": self.observed,
            "threshold": self.threshold,
            "runbook_section": self.runbook_section,
        }


@runtime_checkable
class ModelIntelligenceAlertPort(Protocol):
    def emit(self, alert: ModelIntelligenceAlert) -> None: ...


class NullModelIntelligenceAlertPort:
    def emit(self, alert: ModelIntelligenceAlert) -> None:
        del alert


class ModelIntelligenceAlertEvaluator:
    def __init__(
        self,
        thresholds: ModelIntelligenceAlertThresholds | None = None,
    ) -> None:
        self._thresholds = thresholds or ModelIntelligenceAlertThresholds()

    def _ratio_alert(
        self,
        *,
        alert_id: str,
        reason_code: str,
        observed: float,
        warning: float,
        critical: float,
        runbook_section: str,
    ) -> ModelIntelligenceAlert | None:
        if observed >= critical:
            return ModelIntelligenceAlert(
                alert_id,
                "critical",
                reason_code,
                observed,
                critical,
                runbook_section,
            )
        if observed >= warning:
            return ModelIntelligenceAlert(
                alert_id,
                "warning",
                reason_code,
                observed,
                warning,
                runbook_section,
            )
        return None

    def evaluate(
        self,
        snapshot: ModelIntelligenceOperationalSnapshot,
    ) -> tuple[ModelIntelligenceAlert, ...]:
        resource_ratios = {
            "artifact": snapshot.artifact_bytes / snapshot.artifact_limit_bytes,
            "disk": snapshot.disk_bytes / snapshot.disk_limit_bytes,
            "queue": snapshot.queue_depth / snapshot.queue_limit,
            "ram": snapshot.ram_bytes / snapshot.ram_limit_bytes,
        }
        if snapshot.vram_limit_bytes:
            resource_ratios["vram"] = snapshot.vram_bytes / snapshot.vram_limit_bytes
        alerts: list[ModelIntelligenceAlert] = []
        for resource, ratio in sorted(resource_ratios.items()):
            alert = self._ratio_alert(
                alert_id=f"model_intelligence_{resource}_pressure",
                reason_code=f"{resource}_pressure",
                observed=ratio,
                warning=self._thresholds.resource_warning_ratio,
                critical=self._thresholds.resource_critical_ratio,
                runbook_section=f"failure-scenarios:{resource}",
            )
            if alert is not None:
                alerts.append(alert)
        denominator = max(1, snapshot.completed_jobs)
        for category, count, warning, critical in (
            (
                "failure",
                snapshot.failed_jobs,
                self._thresholds.failure_warning_ratio,
                self._thresholds.failure_critical_ratio,
            ),
            (
                "cancellation",
                snapshot.cancelled_jobs,
                self._thresholds.cancellation_warning_ratio,
                self._thresholds.cancellation_critical_ratio,
            ),
        ):
            alert = self._ratio_alert(
                alert_id=f"model_intelligence_{category}_rate",
                reason_code=f"{category}_rate_high",
                observed=count / denominator,
                warning=warning,
                critical=critical,
                runbook_section=f"failure-scenarios:{category}",
            )
            if alert is not None:
                alerts.append(alert)
        if snapshot.worker_crashes >= self._thresholds.crash_warning_count:
            critical = snapshot.worker_crashes >= self._thresholds.crash_critical_count
            alerts.append(
                ModelIntelligenceAlert(
                    "model_intelligence_worker_crashes",
                    "critical" if critical else "warning",
                    "worker_crash_count_high",
                    float(snapshot.worker_crashes),
                    float(
                        self._thresholds.crash_critical_count
                        if critical
                        else self._thresholds.crash_warning_count
                    ),
                    "failure-scenarios:worker-crash",
                )
            )
        return tuple(sorted(alerts, key=lambda alert: alert.alert_id))

    def evaluate_and_emit(
        self,
        snapshot: ModelIntelligenceOperationalSnapshot,
        port: ModelIntelligenceAlertPort,
    ) -> tuple[ModelIntelligenceAlert, ...]:
        alerts = self.evaluate(snapshot)
        for alert in alerts:
            port.emit(alert)
        return alerts


__all__ = [
    "ModelIntelligenceAlert",
    "ModelIntelligenceAlertError",
    "ModelIntelligenceAlertEvaluator",
    "ModelIntelligenceAlertPort",
    "ModelIntelligenceAlertThresholds",
    "ModelIntelligenceOperationalSnapshot",
    "NullModelIntelligenceAlertPort",
]
