from __future__ import annotations

from flask import Flask

from agent.bootstrap.sfu_broadcast_services import (
    SfuBroadcastRetentionJob,
    SfuBroadcastRouteAdapterAttestation,
    initialize_sfu_broadcast_hub_composition,
)
from agent.services.sfu_broadcast_route_port import ROUTE_PORT_CONTRACT_V1
from agent.services.sfu_fanout_traffic_projection import SfuFanoutRouteClass


class _AudienceRetentionJob:
    def __init__(self) -> None:
        self.calls = 0

    def run(self, context):
        self.calls += 1
        return "next-audience-cursor"


class _CleanupPort:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def purge_expired(self, *, now_ms: int, limit: int) -> int:
        self.calls.append(("purge_expired", limit))
        return 0

    def rotate_envelopes(self, *, limit: int) -> int:
        self.calls.append(("rotate_envelopes", limit))
        return 0

    def purge(self, *, now_ms: int, limit: int) -> int:
        self.calls.append(("purge", limit))
        return 0


class _VendorCleanupPort:
    def __init__(self) -> None:
        self.calls = 0

    def purge_expired(self, *, now: float, limit: int) -> int:
        self.calls += 1
        return 0


class _Context:
    batch_size_max = 17

    def __init__(self) -> None:
        self.lease_checks = 0

    def require_lease(self) -> None:
        self.lease_checks += 1


class _CompleteRouteAdapter:
    def apply(self, command):
        raise AssertionError("composition must not invoke the adapter at startup")

    def update(self, command):
        raise AssertionError("composition must not invoke the adapter at startup")

    def revoke(self, command):
        raise AssertionError("composition must not invoke the adapter at startup")

    def observe(self, query):
        raise AssertionError("composition must not invoke the adapter at startup")


def _app() -> Flask:
    app = Flask(__name__)
    app.secret_key = "sfu-composition-test-secret-with-at-least-32-bytes"
    return app


def test_default_composition_is_profile_gated_and_route_fail_closed() -> None:
    app = _app()
    app.extensions["sfu_audience_retention_job"] = _AudienceRetentionJob()

    composition = initialize_sfu_broadcast_hub_composition(app)

    assert composition.capacity_profile.room_admission_cap == 8
    assert composition.capacity_profile.gate_passed is False
    decision = composition.traffic_projection.classify(
        parent_message_kind="new-unknown-kind",
        parent_schema_version="ananta.webrtc-datachannel.v1",
    )
    assert decision.route_class is SfuFanoutRouteClass.FORBIDDEN_FOR_SFU
    assert composition.route_ports is None
    assert composition.statuses["route_adapter"].ready is False
    assert app.extensions["sfu_broadcast_group_key_repository"] is not None
    assert app.extensions["sfu_group_projection_service"] is not None
    assert app.extensions.get("sfu_layer_projection_service") is None
    assert composition.statuses["layer_projection_signing"].reason_code == (
        "sfu_projection_private_key_unavailable"
    )
    assert app.extensions["sfu_browser_capability_ingestion_service"] is not None
    assert isinstance(app.extensions["sfu_broadcast_retention_job"], SfuBroadcastRetentionJob)


def test_route_adapter_requires_contract_attestation_before_internal_wiring() -> None:
    app = _app()
    adapter = _CompleteRouteAdapter()
    app.extensions["sfu_broadcast_route_adapter"] = adapter
    app.extensions["sfu_broadcast_route_adapter_attestation"] = (
        SfuBroadcastRouteAdapterAttestation(
            adapter_id="selected-runtime-adapter",
            contract_version=ROUTE_PORT_CONTRACT_V1,
            contract_suite_digest="a" * 64,
            passed=True,
        )
    )

    composition = initialize_sfu_broadcast_hub_composition(app)

    assert composition.route_ports is not None
    assert composition.statuses["route_adapter"].ready is True
    assert app.extensions["sfu_broadcast_apply_route_port"] is adapter
    assert composition.statuses["route_reconciler"].reason_code == (
        "sfu_route_reconciliation_state_ports_unavailable"
    )


def test_composite_retention_job_keeps_key_and_projection_receipts_bounded() -> None:
    audience = _AudienceRetentionJob()
    group_keys = _CleanupPort()
    browser = _CleanupPort()
    layers = _CleanupPort()
    vendor = _VendorCleanupPort()
    context = _Context()
    job = SfuBroadcastRetentionJob(
        audience_job=audience,
        group_keys=group_keys,
        browser_capabilities=browser,
        layer_projections=layers,
        vendor_identities=vendor,
        clock=lambda: 100.0,
    )

    cursor = job.run(context)

    assert cursor == "next-audience-cursor"
    assert group_keys.calls == [("purge_expired", 17), ("rotate_envelopes", 17)]
    assert browser.calls == [("purge", 17)]
    assert layers.calls == [("purge", 17)]
    assert vendor.calls == 1
    assert context.lease_checks >= 3
