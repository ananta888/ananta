import hashlib
import json

from agent.services.sfu_runtime_capability_evaluator import (
    SfuRuntimeCapabilityEvaluator,
    SfuRuntimeCapabilityPolicy,
    SfuRuntimeObservationTrust,
)


NOW = 1_700_000_000_000
CONFIG_DIGEST = "sha256:" + "a" * 64
IMAGE_DIGEST = "sha256:" + "b" * 64


def document(*, measured_at=NOW, receiver_limit=500, cpu_ratio=0.2):
    capabilities = [{"name": "route_epoch_fencing", "state": "supported"}]
    capability_digest = "sha256:" + hashlib.sha256(
        json.dumps(capabilities, sort_keys=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()
    return {
        "schema_version": "sfu_runtime_observation.v2",
        "producer_mode": "livekit_control_api",
        "scope": {
            "tenant_id": "tenant-a",
            "cluster_id": "cluster-a",
            "region": "eu-central",
            "observed_node_id": "vendor-node-a",
            "node_binding_authority": "non_authoritative_observation",
        },
        "producer_id": "hub-collector-a",
        "producer_fencing_token": 7,
        "boot_id": "boot-a",
        "sequence": 4,
        "measured_at_ms": measured_at,
        "valid_until_ms": measured_at + 30_000,
        "config_digest": CONFIG_DIGEST,
        "image_digest": IMAGE_DIGEST,
        "capability_digest": capability_digest,
        "capabilities": capabilities,
        "health": {
            "liveness": True,
            "control_ready": True,
            "media_ready": True,
            "admission_ready": True,
        },
        "capacity": {
            "receiver_limit": receiver_limit,
            "room_limit": 20,
            "egress_bps": 10_000_000,
            "memory_bytes_limit": 1_073_741_824,
        },
        "pressure": {
            "cpu_ratio": cpu_ratio,
            "memory_ratio": 0.2,
            "fd_ratio": 0.1,
            "udp_port_ratio": 0.1,
            "packet_drop_ratio": 0.0,
        },
        "labels": {"source": "livekit_server_api_metrics"},
        "proof": None,
    }


def evaluator():
    return SfuRuntimeCapabilityEvaluator(
        SfuRuntimeCapabilityPolicy(
            producer_mode="livekit_control_api",
            config_digest=CONFIG_DIGEST,
            image_digest=IMAGE_DIGEST,
            allowed_capabilities=frozenset({"route_epoch_fencing"}),
            required_capabilities=frozenset({"route_epoch_fencing"}),
            receiver_limit_max=250,
            room_limit_max=10,
            egress_bps_max=5_000_000,
            memory_bytes_max=1_073_741_824,
        )
    )


def test_observed_capacity_can_only_reduce_hub_caps_and_node_is_not_authoritative():
    result = evaluator().evaluate(
        document(),
        SfuRuntimeObservationTrust(transport_authenticated=True, signature_verified=False),
        now_ms=NOW,
    )

    assert result.admission_allowed is True
    assert result.effective_receiver_limit == 250
    assert result.effective_room_limit == 10
    assert result.effective_egress_bps == 5_000_000
    assert result.observed_node_id == "vendor-node-a"
    assert result.observed_node_authoritative is False


def test_stale_or_pressure_observation_becomes_unknown_and_zero_capacity():
    stale = evaluator().evaluate(
        document(measured_at=NOW - 60_000, cpu_ratio=0.95),
        SfuRuntimeObservationTrust(transport_authenticated=True, signature_verified=False),
        now_ms=NOW,
    )

    assert stale.status == "unknown"
    assert stale.admission_allowed is False
    assert stale.effective_receiver_limit == 0
    assert "sfu_runtime_observation_stale" in stale.reason_codes
    assert "sfu_runtime_observation_pressure_exceeded" in stale.reason_codes

