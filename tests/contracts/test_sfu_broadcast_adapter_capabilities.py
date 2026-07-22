from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from agent.services.sfu_broadcast_capability_port import (
    AdapterPathRequirement,
    BASE006_ARTIFACT_PATH,
    BASE006_GATE_ID,
    Base006BroadcastCapabilityAdapter,
    CapabilityKind,
    CapabilityReasonCode,
    CapabilityStatus,
    CapabilitySupportGate,
)


def _report(*rows: dict[str, object], decision: str = "blocked") -> dict[str, object]:
    return {
        "schema": "ananta.livekit-broadcast-runtime-capabilities.v1",
        "gate_id": "SFB-BASE-006",
        "decision": decision,
        "version_binding": {
            "server": {"expected_version": "1.13.1", "runtime_version": None},
            "browser_sdk": {
                "expected_version": "2.20.1",
                "package_version": "2.20.1",
                "lock_version": "2.20.1",
            },
            "source_sha256": "a" * 64,
        },
        "capabilities": list(rows),
    }


def _base006_document() -> dict[str, object]:
    return _report(
        {"capability": "room_service_update_subscriptions", "status": "degraded", "evidence": []},
        {"capability": "simulcast_svc_track_publish_options", "status": "documented", "evidence": []},
        {
            "capability": "data_packet_limits",
            "status": "degraded",
            "limits": {"reliable_payload_bytes": 15360, "lossy_payload_bytes": 1300},
            "evidence": [],
        },
        {"capability": "runtime_route_epoch_queue_fencing", "status": "unsupported", "evidence": []},
        {"capability": "prometheus_metrics", "status": "unsupported", "evidence": []},
        {"capability": "embedded_turn", "status": "documented", "evidence": []},
        {"capability": "native_drain", "status": "documented", "evidence": []},
    )


def test_support_states_are_stable() -> None:
    assert tuple(status.value for status in CapabilityStatus) == (
        "available",
        "degraded",
        "unsupported",
    )


def test_adapter_exposes_distinct_immutable_vendor_neutral_capabilities() -> None:
    snapshot = Base006BroadcastCapabilityAdapter(_base006_document()).capability_snapshot()

    assert {snapshot.support_for(kind).kind for kind in CapabilityKind} == set(CapabilityKind)
    assert snapshot.codec.support.status is CapabilityStatus.UNSUPPORTED
    assert snapshot.simulcast.support.status is CapabilityStatus.DEGRADED
    assert snapshot.svc.support.status is CapabilityStatus.DEGRADED
    assert snapshot.encoded_transform.support.status is CapabilityStatus.UNSUPPORTED
    assert snapshot.server_subscription.support.status is CapabilityStatus.DEGRADED
    assert snapshot.data_packet.support.status is CapabilityStatus.DEGRADED
    assert snapshot.data_stream.support.status is CapabilityStatus.UNSUPPORTED
    assert snapshot.queue_hook.support.status is CapabilityStatus.UNSUPPORTED
    assert snapshot.metrics.support.status is CapabilityStatus.UNSUPPORTED
    assert snapshot.turn.support.status is CapabilityStatus.DEGRADED
    assert snapshot.drain.support.status is CapabilityStatus.DEGRADED
    assert snapshot.data_packet.reliable_payload_bytes == 15360
    assert snapshot.data_packet.lossy_payload_bytes == 1300

    reference = snapshot.server_subscription.support.evidence[0]
    assert reference.gate_id == BASE006_GATE_ID
    assert reference.artifact_path == BASE006_ARTIFACT_PATH
    assert not reference.grounded
    with pytest.raises(FrozenInstanceError):
        snapshot.codec.codecs = ("vp8",)  # type: ignore[misc]


def test_missing_documented_and_combined_features_never_become_available() -> None:
    snapshot = Base006BroadcastCapabilityAdapter(
        _report(
            {"capability": "simulcast_svc_track_publish_options", "status": "available"},
            {"capability": "native_drain", "status": "documented"},
            decision="go",
        )
    ).capability_snapshot()

    assert snapshot.codec.support.status is CapabilityStatus.UNSUPPORTED
    assert snapshot.simulcast.support.status is CapabilityStatus.DEGRADED
    assert snapshot.svc.support.status is CapabilityStatus.DEGRADED
    assert CapabilityReasonCode.COMBINED_EVIDENCE_ONLY.value in snapshot.svc.support.reason_codes
    assert snapshot.drain.support.status is CapabilityStatus.DEGRADED


def test_unprovided_source_identifier_cannot_ground_an_available_claim() -> None:
    document = _report(
        {
            "capability": "codec",
            "status": "available",
            "codecs": ["vp8"],
            "source_ids": ["SRC_NOT_PROVIDED"],
        },
        decision="go",
    )

    support = Base006BroadcastCapabilityAdapter(document).codec_capability().support

    assert support.status is CapabilityStatus.DEGRADED
    assert CapabilityReasonCode.EVIDENCE_UNVERIFIED.value in support.reason_codes
    assert support.evidence[0].source_ids == ()


def test_degraded_and_unsupported_capabilities_disable_dependent_flags() -> None:
    gate = CapabilitySupportGate(Base006BroadcastCapabilityAdapter(_base006_document()))
    requested = {
        "semantic_media_broadcast": True,
        "semantic_media_receiver_groups": True,
        "semantic_media_fleet_admission": True,
        "semantic_media_turn_cost_controls": True,
        "semantic_media_simulcast": True,
        "semantic_media_svc": True,
        "semantic_media_data_fanout": True,
        "semantic_media_data_stream": True,
        "semantic_media_runtime_queue_hook": True,
    }

    resolved = gate.resolve_feature_flags(requested)

    assert resolved == {key: False for key in requested}
    receiver_group = gate.feature_flag_decision("semantic_media_receiver_groups", True)
    assert not receiver_group.flag_enabled
    assert receiver_group.blocking_capabilities == (CapabilityKind.SERVER_SUBSCRIPTION,)


def test_adapter_paths_fail_closed_and_media_path_cannot_drop_e2ee() -> None:
    gate = CapabilitySupportGate(Base006BroadcastCapabilityAdapter(_base006_document()))

    media = gate.adapter_path_decision(
        AdapterPathRequirement(
            "receiver-media",
            (CapabilityKind.CODEC, CapabilityKind.SERVER_SUBSCRIPTION),
            carries_media=True,
        )
    )
    queue = gate.adapter_path_decision(
        AdapterPathRequirement("runtime-queue", (CapabilityKind.QUEUE_HOOK,))
    )
    unbounded = gate.adapter_path_decision(AdapterPathRequirement("unknown", ()))

    assert not media.adapter_allowed
    assert CapabilityKind.ENCODED_TRANSFORM in media.required_capabilities
    assert CapabilityKind.ENCODED_TRANSFORM in media.blocking_capabilities
    assert not queue.adapter_allowed
    assert queue.blocking_capabilities == (CapabilityKind.QUEUE_HOOK,)
    assert not unbounded.adapter_allowed
    assert unbounded.reason_codes == (CapabilityReasonCode.ADAPTER_REQUIREMENTS_MISSING.value,)


def test_malformed_or_vendor_shaped_input_is_absorbed_as_unsupported() -> None:
    document = {
        "schema": "wrong",
        "gate_id": "wrong",
        "capabilities": [{"capability": "codec", "status": RuntimeError("vendor failure"), "token": object()}],
    }

    snapshot = Base006BroadcastCapabilityAdapter(document).capability_snapshot()

    assert all(snapshot.support_for(kind).status is CapabilityStatus.UNSUPPORTED for kind in CapabilityKind)
    assert all(
        CapabilityReasonCode.ARTIFACT_INVALID.value in snapshot.support_for(kind).reason_codes
        for kind in CapabilityKind
    )
