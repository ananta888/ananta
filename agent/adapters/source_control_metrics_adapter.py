"""Prometheus adapter for the closed Source Control Center metric contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from agent.services.source_control_observability import (
    SourceControlMetricsPort,
    SourceControlObservabilityError,
    bounded_metric_labels,
)


@dataclass(frozen=True)
class SourceControlMetricInstruments:
    operations_total: Any
    duration_seconds: Any
    health: Any
    alert_state: Any
    shadow_differences_total: Any


def default_source_control_metric_instruments(
) -> SourceControlMetricInstruments:
    from agent.metrics import (
        SOURCE_CONTROL_ALERT_STATE,
        SOURCE_CONTROL_HEALTH,
        SOURCE_CONTROL_OPERATIONS_TOTAL,
        SOURCE_CONTROL_OPERATION_DURATION_SECONDS,
        SOURCE_CONTROL_SHADOW_DIFFERENCES_TOTAL,
    )

    return SourceControlMetricInstruments(
        operations_total=SOURCE_CONTROL_OPERATIONS_TOTAL,
        duration_seconds=SOURCE_CONTROL_OPERATION_DURATION_SECONDS,
        health=SOURCE_CONTROL_HEALTH,
        alert_state=SOURCE_CONTROL_ALERT_STATE,
        shadow_differences_total=SOURCE_CONTROL_SHADOW_DIFFERENCES_TOTAL,
    )


class PrometheusSourceControlMetrics(SourceControlMetricsPort):
    """Map named observations to fixed instruments and fixed label sets."""

    def __init__(
        self,
        instruments: SourceControlMetricInstruments | None = None,
    ) -> None:
        self._instruments = (
            instruments or default_source_control_metric_instruments()
        )

    def observe_duration(
        self,
        metric: str,
        seconds: float,
        labels: Mapping[str, str],
    ) -> None:
        normalized = bounded_metric_labels(**dict(labels))
        if metric != "source_control_operation_duration_seconds" or set(
            normalized
        ) != {"operation", "status"}:
            raise SourceControlObservabilityError(
                "source_control_duration_metric_invalid"
            )
        self._instruments.duration_seconds.labels(**normalized).observe(
            max(float(seconds), 0.0)
        )

    def increment(
        self,
        metric: str,
        labels: Mapping[str, str],
    ) -> None:
        normalized = bounded_metric_labels(**dict(labels))
        if metric == "source_control_operations_total":
            if set(normalized) != {
                "operation",
                "decision",
                "reason_code",
                "status",
            }:
                raise SourceControlObservabilityError(
                    "source_control_operation_metric_invalid"
                )
            self._instruments.operations_total.labels(**normalized).inc()
            return
        if metric == "source_control_shadow_differences_total":
            if set(normalized) != {"operation", "decision", "status"}:
                raise SourceControlObservabilityError(
                    "source_control_shadow_metric_invalid"
                )
            self._instruments.shadow_differences_total.labels(
                **normalized
            ).inc()
            return
        raise SourceControlObservabilityError(
            "source_control_counter_metric_unknown"
        )

    def set_gauge(
        self,
        metric: str,
        value: float,
        labels: Mapping[str, str],
    ) -> None:
        normalized = bounded_metric_labels(**dict(labels))
        if metric == "source_control_health":
            if set(normalized) != {"status"}:
                raise SourceControlObservabilityError(
                    "source_control_health_metric_invalid"
                )
            self._instruments.health.labels(**normalized).set(float(value))
            return
        if metric == "source_control_alert_state":
            if set(normalized) != {"reason_code", "status"}:
                raise SourceControlObservabilityError(
                    "source_control_alert_metric_invalid"
                )
            self._instruments.alert_state.labels(**normalized).set(
                float(value)
            )
            return
        raise SourceControlObservabilityError(
            "source_control_gauge_metric_unknown"
        )


__all__ = [
    "PrometheusSourceControlMetrics",
    "SourceControlMetricInstruments",
    "default_source_control_metric_instruments",
]
