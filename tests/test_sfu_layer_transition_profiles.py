from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from agent.services.sfu_layer_transition_profile_policy import (
    BoundedLocalLayerTransitionController,
    LayerTransitionProfileRequest,
    LocalLayerTransitionState,
    SfuLayerTransitionProfileError,
    SfuLayerTransitionProfilePolicy,
)
from agent.services.sfu_receiver_layer_policy import HmacReceiverLayerPolicySigner, LayerCorridor, LayerPoint

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "sfu_broadcast_layer_transition_profiles.json"
SCHEMA = json.loads((ROOT / "schemas/webrtc/sfu_layer_transition_profile.v1.json").read_text(encoding="utf-8"))
SIGNER = HmacReceiverLayerPolicySigner(b"transition-profile-test-secret-32+", key_id="transition-test")
NOW = 1_750_000_000_000


def request(*, corridor=None, **overrides):
    values = {
        "tenant_ref": "tenant-a", "room_ref": "sfu-0123456789abcdef0123456789abcdef",
        "subscriber_ref": "subscriber-a", "subscription_ref": "subscription-a",
        "publication_ref": "publication-a",
        "allowed_layer_corridor": corridor or LayerCorridor(LayerPoint(0, 0), LayerPoint(3, 3)),
        "route_epoch": 7, "key_epoch": 11, "issued_at_ms": NOW,
    }
    values.update(overrides)
    return LayerTransitionProfileRequest(**values)


def test_issued_profile_is_deterministic_signed_bounded_and_schema_valid():
    policy = SfuLayerTransitionProfilePolicy(SIGNER, CONFIG)
    first = policy.issue(request())
    second = policy.issue(request())
    assert first.payload() == second.payload()
    Draft202012Validator(SCHEMA).validate(first.payload())
    strategy = first.strategy
    assert strategy["downgrade_dwell_ms"] < strategy["upgrade_dwell_ms"]
    assert strategy["keyframe_retry_max"] <= 3
    assert "frame_callback" not in json.dumps(first.payload())
    assert "packet_callback" not in json.dumps(first.payload())


def test_stale_route_key_and_expired_profiles_are_rejected_locally():
    policy = SfuLayerTransitionProfilePolicy(SIGNER, CONFIG)
    profile = policy.issue(request())
    assert policy.validate_scope(profile, route_epoch=8, key_epoch=11, now_ms=NOW + 1) == "transition_profile_stale_route_epoch"
    assert policy.validate_scope(profile, route_epoch=7, key_epoch=12, now_ms=NOW + 1) == "transition_profile_stale_key_epoch"
    assert policy.validate_scope(profile, route_epoch=7, key_epoch=11, now_ms=NOW + 15_000) == "transition_profile_expired"


def test_downshift_dwell_is_faster_and_upshift_requests_only_bounded_keyframes():
    profile = SfuLayerTransitionProfilePolicy(SIGNER, CONFIG).issue(request())
    controller = BoundedLocalLayerTransitionController()
    state = LocalLayerTransitionState(current=LayerPoint(2, 2))
    started = controller.step(
        profile, state, desired=LayerPoint(0, 0), quality_score=200,
        route_epoch=7, key_epoch=11, now_ms=NOW + 1,
    )
    down = controller.step(
        profile, started.state, desired=LayerPoint(0, 0), quality_score=200,
        route_epoch=7, key_epoch=11, now_ms=NOW + 1_001,
    )
    assert down.action == "downshift" and not down.request_keyframe
    up_started = controller.step(
        profile, down.state, desired=LayerPoint(2, 2), quality_score=900,
        route_epoch=7, key_epoch=11, now_ms=NOW + 2_501,
    )
    up = controller.step(
        profile, up_started.state, desired=LayerPoint(2, 2), quality_score=900,
        route_epoch=7, key_epoch=11, now_ms=NOW + 7_501,
    )
    assert up.action == "upshift" and up.request_keyframe
    retry = controller.step(
        profile, up.state, desired=LayerPoint(2, 2), quality_score=900,
        route_epoch=7, key_epoch=11, now_ms=NOW + 8_501,
    )
    limited = controller.step(
        profile, retry.state, desired=LayerPoint(2, 2), quality_score=900,
        route_epoch=7, key_epoch=11, now_ms=NOW + 9_501,
    )
    assert retry.request_keyframe is True
    assert limited.request_keyframe is False


def test_single_layer_profile_disables_transitions_and_keyframe_retries():
    point = LayerPoint(0, 0)
    profile = SfuLayerTransitionProfilePolicy(SIGNER, CONFIG).issue(
        request(corridor=LayerCorridor(point, point)),
    )
    Draft202012Validator(SCHEMA).validate(profile.payload())
    assert profile.strategy["layer_changes_enabled"] is False
    assert profile.strategy["transitions_per_minute_max"] == 0
    result = BoundedLocalLayerTransitionController().step(
        profile, LocalLayerTransitionState(current=point), desired=point, quality_score=1000,
        route_epoch=7, key_epoch=11, now_ms=NOW + 1,
    )
    assert result.reason_code == "transition_single_layer_hold"


def test_zero_dwell_unbounded_retry_and_future_profile_versions_fail_closed(tmp_path: Path):
    raw = json.loads(CONFIG.read_text(encoding="utf-8"))
    raw["profiles"]["receiver-default-v1"]["upgrade_dwell_ms"] = 0
    path = tmp_path / "transition.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(SfuLayerTransitionProfileError, match="transition_dwell_invalid"):
        SfuLayerTransitionProfilePolicy(SIGNER, path).issue(request())
    raw["profiles"]["receiver-default-v1"]["upgrade_dwell_ms"] = 5000
    raw["profiles"]["receiver-default-v1"]["keyframe_retry_max"] = 999
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(SfuLayerTransitionProfileError, match="transition_keyframe_retry_invalid"):
        SfuLayerTransitionProfilePolicy(SIGNER, path).issue(request())
