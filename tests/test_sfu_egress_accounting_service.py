from __future__ import annotations

from dataclasses import replace

from agent.services.sfu_capacity_feedback_service import (
    SfuCapacityFeedbackSample,
    SfuCapacityFeedbackService,
)
from agent.services.sfu_egress_accounting_service import (
    InMemorySfuEgressAccountingRepository,
    SfuEgressAccountingService,
    SfuEgressAccountingWindow,
)


NOW = 1_800_000_000.0


class _Signatures:
    def verify(self, unsigned, signature):
        return signature == {"key_id": "node-a", "value": "signed"}


def _window(**updates):
    value = SfuEgressAccountingWindow(
        "tenant-a", "room-a", "pub-a", "node-a", "boot-a", 1,
        int(NOW * 1000) - 1000, int(NOW * 1000), 3, 4, 5,
        100_000, 101_000, 102_000,
        {"spatial_0": 25_000, "spatial_1": 75_000}, 40_000, 2,
        {"key_id": "node-a", "value": "signed"},
    )
    return replace(value, **updates)


def _service(repository=None):
    return SfuEgressAccountingService(
        repository or InMemorySfuEgressAccountingRepository(), _Signatures(),
        accounting_tolerance_bytes=4096, accounting_tolerance_percent=5,
        clock=lambda: NOW,
    )


def test_actual_window_is_proportional_and_duplicate_is_idempotent() -> None:
    service = _service()
    first = service.ingest(_window())
    duplicate = service.ingest(_window())
    assert first.status == "accepted" and first.record.value_kind == "actual"
    assert first.record.network_egress_bytes == 100_000
    assert first.record.shared_processing_bytes_saved == 40_000
    assert duplicate.status == "duplicate" and duplicate.record == first.record


def test_gap_restart_regression_and_route_divergence_are_reason_coded() -> None:
    repository = InMemorySfuEgressAccountingRepository()
    service = _service(repository)
    assert service.ingest(_window()).status == "accepted"
    gap = service.ingest(_window(
        sequence=3, window_started_at_ms=int(NOW * 1000) + 100,
        window_ended_at_ms=int(NOW * 1000) + 1100,
        routed_egress_bytes=200_000,
    ))
    assert "sfu_accounting_counter_gap" in gap.record.reconciliation_reason_codes
    assert "sfu_accounting_route_egress_divergence" in gap.record.reconciliation_reason_codes
    regression = service.ingest(_window(
        sequence=2, window_started_at_ms=int(NOW * 1000) + 1200,
        window_ended_at_ms=int(NOW * 1000) + 2200,
    ))
    assert regression.reason_code == "sfu_accounting_counter_regression"
    restarted = service.ingest(_window(
        boot_id="boot-b", sequence=1,
        window_started_at_ms=int(NOW * 1000) + 2300,
        window_ended_at_ms=int(NOW * 1000) + 3300,
    ))
    assert "sfu_accounting_node_restart" in restarted.record.reconciliation_reason_codes


def test_capacity_feedback_never_expands_parent_caps_and_unknown_denies() -> None:
    feedback = SfuCapacityFeedbackService(clock=lambda: NOW)
    base = dict(
        tenant_id="tenant-a", room_id="room-a", publication_id="pub-a", node_id="node-a",
        route_epoch=3, topology_epoch=4, fencing_token=5,
        observed_at_ms=int(NOW * 1000), expires_at_ms=int(NOW * 1000) + 10_000,
        aggregate_window_count=3, hard_egress_bps_max=1000, active_receivers=4,
        current_receiver_cap=8, current_spatial_layer_cap=2,
    )
    high = feedback.evaluate(SfuCapacityFeedbackSample(
        **base, value_kind="actual", egress_bps=950,
    ))
    assert not high.admission_allowed and high.receiver_cap <= 8 and high.spatial_layer_cap == 0
    missing = feedback.evaluate(SfuCapacityFeedbackSample(
        **base, value_kind="missing", egress_bps=None,
        reconciliation_reason_codes=("sfu_accounting_missing_window",),
    ))
    assert not missing.admission_allowed and missing.spatial_layer_cap == 0
