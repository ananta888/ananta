from __future__ import annotations

import pytest

from agent.services.sfu_receiver_layer_policy import (
    HmacReceiverLayerPolicySigner,
    LayerCorridor,
    LayerPoint,
    ReceiverLayerPolicyRequest,
    SfuReceiverLayerPolicy,
    SfuReceiverLayerPolicyError,
)

SIGNER = HmacReceiverLayerPolicySigner(b"receiver-layer-test-secret-value-32+", key_id="receiver-policy-test")
FULL = LayerCorridor(LayerPoint(0, 0), LayerPoint(3, 3))


def request(**overrides):
    values = {
        "tenant_ref": "tenant-a", "room_ref": "sfu-0123456789abcdef0123456789abcdef",
        "subscriber_ref": "subscriber-a", "subscription_ref": "subscription-a",
        "publication_ref": "publication-a", "media_kind": "video",
        "hub_corridor": FULL, "publication_corridor": FULL, "e2ee_corridor": FULL,
        "codec_corridor": FULL, "cost_corridor": FULL, "capacity_corridor": FULL,
        "viewport_class": "large", "user_intent": "detail", "quality_class": "good",
        "last_observation_sequence": 41, "issued_at_ms": 1_750_000_000_000,
    }
    values.update(overrides)
    return ReceiverLayerPolicyRequest(**values)


def test_identical_normalized_subscription_input_is_signed_deterministically():
    policy = SfuReceiverLayerPolicy(SIGNER)
    first = policy.decide(request()).payload()
    second = policy.decide(request()).payload()
    assert first == second
    assert first["allowed_layer_corridor"] == FULL.payload()
    assert first["ttl_ms"] == 10_000
    assert first["reevaluate_not_before_ms"] == first["issued_at_ms"] + 3_000
    assert first["authorization_effect"] == "narrow_only"


def test_cost_capacity_and_user_intent_can_only_narrow_authorized_layers():
    constrained = LayerCorridor(LayerPoint(0, 0), LayerPoint(1, 1))
    result = SfuReceiverLayerPolicy(SIGNER).decide(request(
        cost_corridor=constrained, capacity_corridor=constrained, user_intent="detail",
    )).payload()
    assert result["allowed_layer_corridor"]["maximum"] == {"spatial_id": 1, "temporal_id": 1}


def test_weak_receiver_is_subscription_local_and_does_not_change_another_decision():
    policy = SfuReceiverLayerPolicy(SIGNER)
    strong_before = policy.decide(request(subscription_ref="subscription-strong")).payload()
    weak = policy.decide(request(
        subscription_ref="subscription-weak", subscriber_ref="subscriber-weak",
        quality_class="unknown", viewport_class="thumbnail",
    )).payload()
    strong_after = policy.decide(request(subscription_ref="subscription-strong")).payload()
    assert weak["allowed_layer_corridor"]["maximum"] == {"spatial_id": 0, "temporal_id": 0}
    assert strong_after == strong_before


@pytest.mark.parametrize(
    ("overrides", "outcome"),
    [
        ({"viewport_class": "hidden"}, "lowest_safe_layer"),
        ({"quality_class": "unknown", "last_observation_sequence": None}, "lowest_safe_layer"),
        ({"user_intent": "audio_only"}, "deny"),
        ({"media_kind": "audio", "user_intent": "balanced"}, "lowest_safe_layer"),
    ],
)
def test_hidden_unknown_audio_only_and_audio_are_conservative(overrides, outcome):
    assert SfuReceiverLayerPolicy(SIGNER).decide(request(**overrides)).payload()["safe_outcome"] == outcome


def test_multiple_publications_are_independent_and_malicious_quality_is_rejected():
    policy = SfuReceiverLayerPolicy(SIGNER)
    first = policy.decide(request(publication_ref="publication-a")).signature
    second = policy.decide(request(publication_ref="publication-b")).signature
    assert first != second
    with pytest.raises(SfuReceiverLayerPolicyError, match="receiver_layer_quality_invalid"):
        policy.decide(request(quality_class="packets=999999"))
