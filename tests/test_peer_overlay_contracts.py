from __future__ import annotations

from dataclasses import replace

import pytest

from ananta_contracts.peer_overlay import MembershipEventV1, OverlayEpochs, PeerRouteLease


def test_epoch_transitions_separate_membership_route_and_topology() -> None:
    initial = OverlayEpochs(1, 1, 1, 1)
    OverlayEpochs(2, 2, 1, 1).assert_successor(initial, change="membership")
    OverlayEpochs(1, 1, 2, 1).assert_successor(initial, change="route")
    OverlayEpochs(1, 1, 2, 2).assert_successor(initial, change="topology")
    with pytest.raises(ValueError, match="epoch_transition_invalid"):
        OverlayEpochs(2, 1, 1, 1).assert_successor(initial, change="membership")


def test_route_lease_is_scope_bound_expiring_and_tamper_evident() -> None:
    key = b"l" * 32
    lease = PeerRouteLease(
        version=1,
        lease_id="lease-1",
        tenant_id="tenant-1",
        room_id="room-1",
        publication_id="publication-1",
        child_peer_id="child-1",
        primary_parent_id="parent-1",
        backup_parent_id="parent-2",
        epochs=OverlayEpochs(1, 1, 2, 2),
        capabilities=("data_relay", "direct"),
        traffic_classes=("control", "bulk"),
        max_hops=2,
        issued_at="2026-08-29T00:00:00Z",
        expires_at="2026-08-29T00:01:00Z",
        nonce="nonce-1",
        hub_key_id="hub-key-1",
    ).sign(key)
    lease.verify(
        key,
        now="2026-08-29T00:00:30Z",
        tenant_id="tenant-1",
        room_id="room-1",
        publication_id="publication-1",
        child_peer_id="child-1",
        minimum_epochs=OverlayEpochs(1, 1, 2, 2),
    )
    with pytest.raises(ValueError, match="signature_invalid"):
        replace(lease, max_hops=3).verify(
            key,
            now="2026-08-29T00:00:30Z",
            tenant_id="tenant-1",
            room_id="room-1",
            publication_id="publication-1",
            child_peer_id="child-1",
            minimum_epochs=OverlayEpochs(1, 1, 2, 2),
        )


def test_device_replacement_event_is_signed_and_rejects_forks() -> None:
    key = b"m" * 32
    event = MembershipEventV1(
        version=1,
        event_id="event-2",
        tenant_id="tenant-1",
        room_id="room-1",
        sequence=2,
        previous_digest="a" * 64,
        action="device_replace",
        subject_peer_id="device-old",
        replacement_peer_id="device-new",
        member_ids=("device-new", "peer-2"),
        epochs=OverlayEpochs(2, 2, 1, 1),
        issued_at="2026-08-29T00:00:00Z",
        expires_at="2026-08-29T00:01:00Z",
        hub_key_id="hub-key-1",
    ).sign(key)
    event.verify(
        key,
        expected_hub_key_id="hub-key-1",
        now="2026-08-29T00:00:30Z",
        tenant_id="tenant-1",
        room_id="room-1",
        expected_sequence=2,
        expected_previous_digest="a" * 64,
    )
    with pytest.raises(ValueError, match="fork_or_gap"):
        event.verify(
            key,
            expected_hub_key_id="hub-key-1",
            now="2026-08-29T00:00:30Z",
            tenant_id="tenant-1",
            room_id="room-1",
            expected_sequence=3,
            expected_previous_digest=event.event_digest,
        )
    with pytest.raises(ValueError, match="hub_key_unknown"):
        event.verify(
            key,
            expected_hub_key_id="retired-hub-key",
            now="2026-08-29T00:00:30Z",
            tenant_id="tenant-1",
            room_id="room-1",
            expected_sequence=2,
            expected_previous_digest="a" * 64,
        )
