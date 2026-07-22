import json
from pathlib import Path

import pytest

from agent.adapters.sfu_broadcast_prometheus_metrics_adapter import SfuBroadcastPrometheusMetricsAdapter
from agent.services.sfu_broadcast_metrics_port import SfuBroadcastAuditRule
from agent.services.sfu_broadcast_metrics_service import (
    SfuBroadcastMetricBufferLimits,
    SfuBroadcastMetricsError,
    SfuBroadcastMetricsService,
)
from agent.services.sfu_broadcast_observability_policy import SfuBroadcastObservabilityPolicy


ROOT = Path(__file__).resolve().parents[1]


class _Sink:
    def __init__(self):
        self.fail = False
        self.values = []

    def _add(self, value):
        if self.fail:
            raise RuntimeError("SENSITIVE-SINK-FAILURE")
        self.values.append(value)

    increment_counter = _add
    observe_histogram = _add
    set_gauge = _add
    emit_audit_event = _add


def _policy():
    catalog = json.loads((ROOT / "config/sfu_broadcast_observability_catalog.json").read_text())
    return SfuBroadcastObservabilityPolicy(catalog, pseudonym_secret=b"m" * 32)


def test_catalog_kind_unit_labels_and_value_bounds_are_enforced_without_echo():
    sink = _Sink()
    service = SfuBroadcastMetricsService(
        policy=_policy(), counter_port=sink, histogram_port=sink, gauge_port=sink, audit_port=sink
    )
    common = dict(
        metric_name="ananta_sfu_broadcast_join_latency",
        unit="milliseconds",
        value=25,
        labels={"outcome": "accepted", "transport": "direct"},
        scope_id="ROOM-PRIVATE-CANARY",
        cohort_size=10,
        now_seconds=3600,
    )
    assert service.observe(**common).emitted
    assert "ROOM-PRIVATE-CANARY" not in json.dumps(sink.values[0].public())
    with pytest.raises(SfuBroadcastMetricsError, match="port_kind_mismatch"):
        service.gauge(**common)
    with pytest.raises(SfuBroadcastMetricsError, match="unit_mismatch"):
        service.observe(**{**common, "unit": "seconds"})
    with pytest.raises(SfuBroadcastMetricsError, match="value_bound_exceeded"):
        service.observe(**{**common, "value": 10_001})


def test_sink_failure_is_bounded_retried_and_never_raises_sink_details():
    now = [10.0]
    sink = _Sink()
    sink.fail = True
    service = SfuBroadcastMetricsService(
        policy=_policy(),
        counter_port=sink,
        histogram_port=sink,
        gauge_port=sink,
        audit_port=sink,
        limits=SfuBroadcastMetricBufferLimits(max_items=1, max_bytes=1024, max_age_seconds=5, retry_deadline_seconds=.01),
        clock=lambda: now[0],
    )
    kwargs = dict(
        metric_name="ananta_sfu_broadcast_drop_count", unit="events", value=1,
        labels={"drop_reason": "backpressure"}, scope_id="room-a", cohort_size=10, now_seconds=10,
    )
    assert service.increment(**kwargs).reason_code == "sfu_metrics_export_deferred"
    assert service.increment(**kwargs).reason_code == "sfu_metrics_backpressure_dropped"
    sink.fail = False
    assert service.retry_pending().exported == 1
    assert service.pending_count == 0


def test_closed_audit_catalog_and_prometheus_adapter_export_only_public_dimensions():
    policy = _policy()
    adapter = SfuBroadcastPrometheusMetricsAdapter(policy=policy)
    service = SfuBroadcastMetricsService(
        policy=policy,
        counter_port=adapter,
        histogram_port=adapter,
        gauge_port=adapter,
        audit_port=adapter,
        audit_catalog={
            "sfu_broadcast_control": SfuBroadcastAuditRule(
                frozenset({"accepted"}), frozenset({"policy_allowed"}), {"path": frozenset({"hub"})}
            )
        },
    )
    service.gauge(
        "ananta_sfu_broadcast_group_members", unit="participants", value=10,
        labels={"operation": "reconcile", "outcome": "accepted"},
        scope_id="ROOM-ORIGINAL", cohort_size=10, now_seconds=3600,
    )
    service.audit("sfu_broadcast_control", outcome="accepted", reason_code="policy_allowed", labels={"path": "hub"}, now_seconds=3600)
    rendered = adapter.render_openmetrics()
    assert "ROOM-ORIGINAL" not in rendered
    assert "scope_pseudonym=\"sfb1." in rendered
    with pytest.raises(SfuBroadcastMetricsError, match="audit_event_not_registered"):
        service.audit("invented", outcome="accepted", reason_code="policy_allowed", labels={}, now_seconds=3600)
