from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from agent.services.sfu_egress_fairness_profile_policy import (
    SfuAggregateBackpressureSample,
    SfuEgressFairnessProfilePolicy,
    SfuFairnessRuntimeCapabilities,
    SfuFairnessScope,
)


NOW = 1_800_000_000.0
ROOT = Path(__file__).resolve().parents[1]


class _Signatures:
    def verify(self, document, signature):
        return signature.get("value") == "x" * 32


def _document(**updates):
    value = {
        "schema": "ananta.sfu-broadcast-fairness-profile.v1", "schema_version": 1,
        "profile_id": "strict-room", "profile_version": 1,
        "tenant_ref": "tenant-a", "room_ref": "room-a", "route_epoch": 3,
        "topology_epoch": 4, "issued_at_ms": int(NOW * 1000),
        "expires_at_ms": int(NOW * 1000) + 30_000,
        "weights": {"receiver": 1, "room": 1, "tenant": 1},
        "hard_limits": {
            "receiver_egress_bps_max": 4_000_000, "room_egress_bps_max": 50_000_000,
            "tenant_egress_bps_max": 500_000_000, "queue_bytes_max": 262_144,
            "active_receivers_max": 8,
        },
        "stages": {
            "downshift_utilization_basis_points": 8000,
            "disconnect_utilization_basis_points": 9800,
            "disconnect_grace_ms": 10_000, "lowest_safe_spatial_layer": 0,
        },
        "rules": {
            "queue_limits": {"value": 262_144, "enforcement": "browser_enforced"},
            "egress_limits": {"value": 50_000_000, "enforcement": "observation_only"},
            "max_starvation_window_ms": {"value": 5000, "enforcement": "observation_only"},
            "min_jain_fairness_index_basis_points": {"value": 8500, "enforcement": "observation_only"},
        },
        "signature": {"algorithm": "HMAC-SHA256", "key_id": "test", "value": "x" * 32},
    }
    value.update(updates)
    return value


def _policy():
    return SfuEgressFairnessProfilePolicy(_Signatures(), clock=lambda: NOW)


def test_catalog_and_signed_runtime_profile_validate() -> None:
    schema = json.loads((ROOT / "schemas/webrtc/sfu_broadcast_fairness_profile.v1.json").read_text())
    catalog = json.loads((ROOT / "config/sfu_broadcast_fairness_profiles.json").read_text())
    Draft202012Validator(schema).validate(catalog)
    Draft202012Validator(schema).validate(_document())
    decision = _policy().resolve(
        json.dumps(_document()), scope=SfuFairnessScope("tenant-a", "room-a", 3, 4),
        capabilities=SfuFairnessRuntimeCapabilities(), parent_egress_bps_max=80_000_000,
        parent_receiver_cap=8,
    )
    assert decision.accepted


def test_unknown_capability_and_widening_return_strict_default() -> None:
    runtime = _document()
    runtime["rules"]["max_starvation_window_ms"]["enforcement"] = "runtime_enforced"
    decision = _policy().resolve(
        json.dumps(runtime), scope=SfuFairnessScope("tenant-a", "room-a", 3, 4),
        capabilities=SfuFairnessRuntimeCapabilities(), parent_egress_bps_max=80_000_000,
        parent_receiver_cap=8,
    )
    assert not decision.accepted
    assert decision.reason_code == "sfu_fairness_runtime_capability_unsupported"
    widened = _document()
    widened["hard_limits"]["room_egress_bps_max"] = 90_000_000
    assert not _policy().resolve(
        json.dumps(widened), scope=SfuFairnessScope("tenant-a", "room-a", 3, 4),
        capabilities=SfuFairnessRuntimeCapabilities(), parent_egress_bps_max=80_000_000,
        parent_receiver_cap=8,
    ).accepted


def test_backpressure_is_aggregate_and_never_raises_layer_cap() -> None:
    profile = _policy().resolve(
        json.dumps(_document()), scope=SfuFairnessScope("tenant-a", "room-a", 3, 4),
        capabilities=SfuFairnessRuntimeCapabilities(), parent_egress_bps_max=80_000_000,
        parent_receiver_cap=8,
    )
    single = _policy().derive_publisher_backpressure(
        profile, SfuAggregateBackpressureSample(1, 1, 1, 1, 2),
    )
    assert not single.admission_allowed and single.spatial_layer_cap == 0
    assert not hasattr(_policy(), "execute") and not hasattr(_policy(), "disconnect")
