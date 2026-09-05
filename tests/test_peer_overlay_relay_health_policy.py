from dataclasses import replace
from datetime import datetime, timedelta, timezone

from agent.services.peer_overlay_relay_health_policy import PeerOverlayRelayHealthPolicy
from ananta_contracts.peer_overlay import (
    OverlayCapability,
    OverlayEpochs,
    OverlayTrafficClass,
    PeerRouteLease,
)


def _lease() -> PeerRouteLease:
    now = datetime.now(timezone.utc)
    return PeerRouteLease(
        version=1,
        lease_id="lease-1",
        tenant_id="tenant-1",
        room_id="room-1",
        publication_id="publication-1",
        child_peer_id="child",
        primary_parent_id="primary",
        backup_parent_id="backup",
        epochs=OverlayEpochs(1, 1, 2, 2),
        capabilities=(OverlayCapability.DATA_RELAY,),
        traffic_classes=(OverlayTrafficClass.EVENT,),
        max_hops=3,
        issued_at=now.isoformat(),
        expires_at=(now + timedelta(minutes=1)).isoformat(),
        nonce="nonce-1",
        hub_key_id="hub-1",
    )


def _observation(observer: str, now_ms: int) -> dict[str, object]:
    return {
        "observer_peer_id": observer,
        "relay_peer_id": "primary",
        "route_epoch": 2,
        "delivery_ratio": 0.5,
        "delay_ms": 4_000,
        "sample_count": 10,
        "observed_at_ms": now_ms,
    }


def test_single_complaint_cannot_ban_a_relay_but_quorum_can_fail_over() -> None:
    policy = PeerOverlayRelayHealthPolicy()
    now = 10_000_000
    single = policy.evaluate(
        lease=_lease(), observations=[_observation("observer-1", now)], now_ms=now, last_failover_at_ms=None
    )
    assert single["switch_to_backup"] is False
    quorum = policy.evaluate(
        lease=_lease(),
        observations=[_observation("observer-1", now), _observation("observer-2", now)],
        now_ms=now,
        last_failover_at_ms=None,
    )
    assert quorum["switch_to_backup"] is True
    assert quorum["permanent_double_traffic_allowed"] is False


def test_stale_epoch_observation_and_cooldown_are_fail_closed() -> None:
    policy = PeerOverlayRelayHealthPolicy()
    now = 10_000_000
    stale = [_observation("observer-1", now), _observation("observer-2", now)]
    stale = [{**item, "route_epoch": 1} for item in stale]
    assert (
        policy.evaluate(lease=_lease(), observations=stale, now_ms=now, last_failover_at_ms=None)["switch_to_backup"]
        is False
    )
    cooled = policy.evaluate(
        lease=_lease(),
        observations=[_observation("observer-1", now), _observation("observer-2", now)],
        now_ms=now,
        last_failover_at_ms=now - 1_000,
    )
    assert cooled["reason_code"] == "peer_overlay_failover_cooldown"


def test_failover_requires_a_signed_backup_candidate() -> None:
    result = PeerOverlayRelayHealthPolicy().evaluate(
        lease=replace(_lease(), backup_parent_id=None),
        observations=[],
        now_ms=10_000_000,
        last_failover_at_ms=None,
    )
    assert result["switch_to_backup"] is False


def test_selective_forwarding_is_detected_per_class_without_multiplying_votes() -> None:
    policy = PeerOverlayRelayHealthPolicy()
    now = 10_000_000
    observations = []
    for observer in ("observer-1", "observer-2"):
        observations.extend(
            [
                {**_observation(observer, now), "traffic_class": "control"},
                {
                    **_observation(observer, now),
                    "traffic_class": "bulk",
                    "delivery_ratio": 1.0,
                    "delay_ms": 20,
                },
            ]
        )
    decision = policy.evaluate(
        lease=_lease(), observations=observations, now_ms=now, last_failover_at_ms=None
    )
    assert decision["switch_to_backup"] is True
    assert decision["affected_traffic_classes"] == ["control"]
    assert decision["complaining_peer_ids"] == ["observer-1", "observer-2"]

    one_observer = policy.evaluate(
        lease=_lease(),
        observations=[
            {**_observation("observer-1", now), "traffic_class": traffic_class}
            for traffic_class in ("control", "rekey", "event", "semantic", "bulk")
        ],
        now_ms=now,
        last_failover_at_ms=None,
    )
    assert one_observer["switch_to_backup"] is False
