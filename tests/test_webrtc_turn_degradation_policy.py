import dataclasses

import pytest

from agent.services.webrtc_turn_degradation_policy import (
    InMemoryTurnReceiverStatePort,
    TurnDegradationSignal,
    WebrtcTurnDegradationError,
    WebrtcTurnDegradationPolicy,
)


def _signal(receiver="receiver-a", version=0, **changes):
    values = dict(
        receiver_ref=receiver, expected_version=version, event="admitted", quota_decision="allow",
        credential_valid=True, pool_available=True, direct_available=False,
        encryption_required=True, encryption_available=True, parent_fallback_allowed=True,
        control_allowed=True, key_allowed=True, transcript_allowed=True,
    )
    values.update(changes)
    return TurnDegradationSignal(**values)


def test_authoritative_states_are_signed_receiver_isolated_and_keep_priority_classes():
    clock = [1000]
    policy = WebrtcTurnDegradationPolicy(InMemoryTurnReceiverStatePort(), signing_secret=b"d" * 32, clock=lambda: clock[0])
    relay = policy.transition(_signal())
    capped = policy.transition(_signal(version=relay.version, event="capacity", quota_decision="lower_cap"))
    other = policy.transition(_signal(receiver="receiver-b"))
    assert relay.state == "relay_ok" and capped.state == "relay_capped"
    assert capped.allowed_layer == "low" and {"control", "key", "transcript", "media"} == set(capped.allowed_classes)
    assert other.state == "relay_ok" and other.version == 1
    assert policy.verify(capped)


def test_exhaustion_cooldown_fallback_and_encryption_never_silently_downgrade():
    clock = [1000]
    policy = WebrtcTurnDegradationPolicy(InMemoryTurnReceiverStatePort(), signing_secret=b"d" * 32, retry_max=1, clock=lambda: clock[0])
    control = policy.transition(_signal(event="capacity", quota_decision="relay_capacity_exhausted"))
    same = policy.transition(_signal(version=control.version, event="pool_unavailable", pool_available=False))
    assert control.state == "control_only" and same.version == control.version
    clock[0] = 1011
    fallback = policy.transition(_signal(version=control.version, event="pool_unavailable", pool_available=False))
    assert fallback.state == "fallback"

    rejected = policy.transition(_signal(receiver="receiver-c", encryption_available=False))
    assert rejected.state == "rejected" and rejected.allowed_classes == ()
    with pytest.raises(WebrtcTurnDegradationError, match="version_conflict"):
        policy.transition(_signal(version=99))
