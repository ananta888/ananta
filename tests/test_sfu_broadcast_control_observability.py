import inspect
import json
from pathlib import Path

from agent.services.semantic_sfu_admission_service import SemanticSfuAdmissionService
from agent.services.semantic_sfu_group_key_service import SemanticSfuGroupKeyService
from agent.services.sfu_all_turn_capacity_gate import SfuAllTurnCapacityGate
from agent.services.sfu_broadcast_control_observability import (
    MetricsSfuBroadcastControlObservationAdapter,
)
from agent.services.sfu_broadcast_metrics_service import SfuBroadcastMetricsService
from agent.services.sfu_broadcast_observability_policy import SfuBroadcastObservabilityPolicy
from agent.services.sfu_capacity_feedback_service import SfuCapacityFeedbackService
from agent.services.sfu_fanout_reconciliation_service import SfuFanoutRouteReconciliationService
from agent.services.sfu_fleet_reconciliation_service import SfuFleetReconciliationService
from agent.services.sfu_group_projection_service import SfuGroupProjectionService
from agent.services.sfu_layer_projection_service import SfuLayerProjectionService
from agent.services.sfu_receiver_quality_ingestion_service import SfuReceiverQualityIngestionService
from agent.services.sfu_runtime_observation_service import SfuRuntimeObservationService
from agent.services.turn_accounting_service import TurnAccountingService


ROOT = Path(__file__).resolve().parents[1]


class _Sink:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.events = []

    def increment_counter(self, point):
        del point

    def observe_histogram(self, point):
        del point

    def set_gauge(self, point):
        del point

    def emit_audit_event(self, event):
        if self.fail:
            raise RuntimeError("sink down")
        self.events.append(event)


def _adapter(*, fail: bool = False):
    catalog = json.loads((ROOT / "config/sfu_broadcast_observability_catalog.json").read_text())
    policy = SfuBroadcastObservabilityPolicy(catalog, pseudonym_secret=b"o" * 32)
    sink = _Sink(fail=fail)
    metrics = SfuBroadcastMetricsService(
        policy=policy,
        counter_port=sink,
        histogram_port=sink,
        gauge_port=sink,
        audit_port=sink,
    )
    return MetricsSfuBroadcastControlObservationAdapter(metrics, clock=lambda: 10.0), sink


def test_registered_control_observation_is_content_free_and_catalog_gated():
    adapter, sink = _adapter()

    result = adapter.record(control_path="key_delivery", outcome="accepted", reason_code="success")

    assert result.recorded is True
    assert sink.events[0].public()["labels"] == {
        "control_path": "key_delivery",
        "plane": "hub",
        "security_scope": "private",
    }


def test_unknown_observation_is_visible_and_not_exported():
    adapter, sink = _adapter()

    result = adapter.record(control_path="custom_runtime_identifier", outcome="accepted", reason_code="success")

    assert result.reason_code == "sfu_control_observation_not_registered"
    assert sink.events == []


def test_sink_failure_is_bounded_without_becoming_a_control_failure():
    adapter, _sink = _adapter(fail=True)

    result = adapter.record(control_path="admission", outcome="accepted", reason_code="success")

    assert result.recorded is False
    assert result.buffered is True
    assert result.reason_code == "sfu_metrics_export_deferred"


def test_real_control_services_expose_optional_observer_di_and_wrapped_decisions():
    services = (
        (SemanticSfuAdmissionService, "join"),
        (SfuGroupProjectionService, "project"),
        (SfuLayerProjectionService, "materialize"),
        (SfuFanoutRouteReconciliationService, "reconcile"),
        (SfuRuntimeObservationService, "ingest"),
        (SfuFleetReconciliationService, "run_once"),
        (TurnAccountingService, "ingest"),
        (SemanticSfuGroupKeyService, "deliver_packages"),
        (SfuReceiverQualityIngestionService, "ingest"),
        (SfuCapacityFeedbackService, "evaluate"),
        (SfuAllTurnCapacityGate, "evaluate"),
    )

    for service, method_name in services:
        assert "control_observer" in inspect.signature(service).parameters
        assert hasattr(getattr(service, method_name), "__wrapped__")
