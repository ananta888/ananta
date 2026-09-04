from __future__ import annotations

from dataclasses import replace

import pytest

from agent.services.collaboration_live_transport import (
    BoundedCollaborationOfflineOutbox,
    CollaborationLiveRouter,
    CollaborationLiveTopologySelector,
    CollaborationTransportCircuitBreaker,
    LiveEnvelope,
    LiveReceiverState,
    collaboration_transport_health,
)


def test_topology_selector_never_promotes_observe_only_sfu() -> None:
    selector = CollaborationLiveTopologySelector(
        sfu_release_state="observe_only", relay_ready=True, contract_revision=4, clock=lambda: 10.0
    )
    automatic = selector.select(participant_count=4, e2ee_ready=True)
    explicit = selector.select(participant_count=4, requested="sfu", e2ee_ready=True)
    closed = selector.select(
        participant_count=4,
        requested="sfu",
        e2ee_ready=True,
        safe_fallback_allowed=False,
    )
    assert (automatic.topology, explicit.topology, closed.topology) == ("relay", "relay", "unavailable")
    assert (explicit.contract_revision, explicit.expires_at) == (4, 40.0)


def test_router_enforces_per_receiver_rights_epoch_and_subscription() -> None:
    states = {
        "publisher": LiveReceiverState("publisher", True, 3, frozenset({"semantic"})),
        "receiver-a": LiveReceiverState("receiver-a", True, 3, frozenset({"semantic"})),
        "receiver-b": LiveReceiverState("receiver-b", False, 3, frozenset({"semantic"})),
        "receiver-c": LiveReceiverState("receiver-c", True, 2, frozenset({"semantic"})),
        "receiver-d": LiveReceiverState("receiver-d", True, 3, frozenset({"presence"})),
    }
    router = CollaborationLiveRouter(lambda _workspace, _room, actor_id: states.get(actor_id), clock=lambda: 1.0)
    envelope = LiveEnvelope(
        "envelope-a",
        "workspace-a",
        "room-a",
        "publisher",
        "semantic",
        3,
        1.0,
        {"delta": "safe"},
    )
    result = router.publish(
        envelope,
        server_selected_receivers=["receiver-a", "receiver-b", "receiver-c", "receiver-d"],
    )
    assert result["recipients"] == ["receiver-a"]
    assert result["dropped"] == {
        "receiver-b": "receiver_membership_inactive",
        "receiver-c": "receiver_epoch_mismatch",
        "receiver-d": "receiver_not_subscribed",
    }
    assert router.receive("receiver-a")[0]["envelope_id"] == "envelope-a"
    duplicate = router.publish(envelope, server_selected_receivers=["receiver-a"])
    assert duplicate["dropped"] == {"receiver-a": "duplicate_envelope"}
    with pytest.raises(ValueError, match="audience_escalation"):
        router.publish(
            replace(envelope, envelope_id="envelope-b", payload={"audience": ["receiver-b"]}),
            server_selected_receivers=["receiver-a"],
        )


def test_slow_receiver_queue_does_not_block_other_receiver_and_revoke_is_immediate() -> None:
    states = {
        actor_id: LiveReceiverState(actor_id, True, 1, frozenset({"semantic"}))
        for actor_id in ("publisher", "slow", "fast")
    }
    router = CollaborationLiveRouter(lambda _workspace, _room, actor_id: states.get(actor_id), clock=lambda: 1.0)
    for index in range(64):
        envelope = LiveEnvelope(
            f"envelope-{index}",
            "workspace-a",
            "room-a",
            "publisher",
            "semantic",
            1,
            1.0,
            {"delta": index},
        )
        router.publish(envelope, server_selected_receivers=["slow", "fast"])
        router.acknowledge("fast", envelope.envelope_id)
    final = LiveEnvelope(
        "envelope-final",
        "workspace-a",
        "room-a",
        "publisher",
        "semantic",
        1,
        1.0,
        {"delta": "final"},
    )
    result = router.publish(final, server_selected_receivers=["slow", "fast"])
    assert result["recipients"] == ["fast"]
    assert result["dropped"] == {"slow": "receiver_backpressure"}
    assert router.revoke("slow") == 64
    states["slow"] = replace(states["slow"], active=False)
    assert (
        router.publish(replace(final, envelope_id="after-revoke"), server_selected_receivers=["slow"])["recipients"]
        == []
    )


def test_durable_projection_requires_hub_event_identity_and_circuit_probe_is_bounded() -> None:
    state = LiveReceiverState("publisher", True, 1, frozenset({"durable_projection"}))
    router = CollaborationLiveRouter(lambda _workspace, _room, _actor: state)
    with pytest.raises(ValueError, match="durable_identity_required"):
        router.publish(
            LiveEnvelope(
                "envelope-a",
                "workspace-a",
                "room-a",
                "publisher",
                "durable_projection",
                1,
                1.0,
                {"sequence": "peer-sequence-is-not-authority"},
            ),
            server_selected_receivers=[],
        )
    breaker = CollaborationTransportCircuitBreaker(failure_threshold=2, backoff_seconds=5)
    breaker.record_failure(10.0)
    breaker.record_failure(11.0)
    assert breaker.allow(12.0) == (False, "transport_backoff")
    assert breaker.allow(16.0) == (True, "transport_bounded_probe")
    breaker.record_success()
    assert breaker.allow(16.0) == (True, "transport_closed")


def test_offline_outbox_is_bounded_idempotent_and_reports_conflicts() -> None:
    current = [1.0]
    outbox = BoundedCollaborationOfflineOutbox(
        maximum_items=2, maximum_bytes=500, ttl_seconds=10, clock=lambda: current[0]
    )
    first = {"event_id": "event-a", "event_type": "message.posted", "payload": {"text": "one"}}
    second = {"event_id": "event-b", "event_type": "message.replied", "payload": {"text": "two"}}
    assert outbox.enqueue(first)["replayed"] is False
    assert outbox.enqueue(first)["replayed"] is True
    outbox.enqueue(second)
    with pytest.raises(OverflowError, match="outbox_full"):
        outbox.enqueue({"event_id": "event-c", "event_type": "message.posted", "payload": {}})
    result = outbox.flush(
        lambda event: {"accepted": event["event_id"] == "event-a", "reason_code": "revision_conflict"}
    )
    assert result == {
        "delivered": ["event-a"],
        "conflicts": [{"event_id": "event-b", "reason_code": "revision_conflict"}],
        "remaining": 1,
    }
    with pytest.raises(ValueError, match="event_type_rejected"):
        outbox.enqueue({"event_id": "event-x", "event_type": "task.projected", "payload": {}})
    current[0] = 12.0
    assert outbox.flush(lambda _event: {"accepted": True})["remaining"] == 0


def test_transport_health_keeps_component_and_projection_diagnostics_separate() -> None:
    components = {
        "signaling": "ready",
        "datachannel": "ready",
        "relay": "ready",
        "sfu": "disabled",
        "turn": "disabled",
        "e2ee": "ready",
    }
    assert collaboration_transport_health(components, projection_lag=2, maximum_projection_lag=5)["state"] == "ready"
    unhealthy = collaboration_transport_health(components, projection_lag=6, maximum_projection_lag=5)
    assert (unhealthy["state"], unhealthy["reason_code"]) == (
        "degraded",
        "transport_component_unhealthy",
    )
