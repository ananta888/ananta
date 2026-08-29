from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent.services.peer_overlay_control_service import PeerOverlayControlService
from agent.services.peer_overlay_state_store import PeerOverlayStateConflict, PeerOverlayStateStore
from agent.services.peer_overlay_topology_service import PeerOverlayTopologyService


def service(tmp_path) -> PeerOverlayControlService:
    key = b"p" * 32
    return PeerOverlayControlService(
        PeerOverlayStateStore(tmp_path / "overlay.sqlite3"),
        signing_key=key,
        hub_key_id="hub-1",
        topology=PeerOverlayTopologyService(key, hub_key_id="hub-1"),
        data_enabled=True,
    )


def join_members(control: PeerOverlayControlService) -> None:
    for revision, peer_id in enumerate(("source", "peer-1", "peer-2")):
        control.change_membership(
            tenant_id="tenant-1",
            room_id="room-1",
            action="join",
            subject_peer_id=peer_id,
            expected_revision=revision,
        )


def candidates() -> list[dict[str, object]]:
    return [
        {
            "peer_id": peer_id,
            "relay_consent": True,
            "visible": True,
            "battery": "mains",
            "network": "fast",
            "self_capacity": 80,
            "observed_capacity": 75,
            "delivery_ratio": 0.99,
        }
        for peer_id in ("source", "peer-1", "peer-2")
    ]


def test_hub_membership_plan_and_one_use_link_ticket_are_fully_automatic(tmp_path) -> None:
    control = service(tmp_path)
    join_members(control)
    plan = control.plan_publication(
        tenant_id="tenant-1",
        room_id="room-1",
        publication_id="publication-1",
        source_peer_id="source",
        candidates=candidates(),
    )
    assert plan["media_forwarding_allowed"] is False
    assert plan["fallback"] == "livekit_e2ee"
    lease = plan["leases"][0]
    ticket = control.issue_link_ticket(
        tenant_id="tenant-1",
        room_id="room-1",
        publication_id="publication-1",
        lease_id=lease["lease_id"],
    )
    result = control.consume_link_ticket(ticket=ticket, local_peer_id=lease["child_peer_id"])
    assert result["accepted"] is True
    with pytest.raises(PeerOverlayStateConflict, match="ticket_replayed"):
        control.consume_link_ticket(ticket=ticket, local_peer_id=lease["child_peer_id"])
    assert control.overview(room_id="room-1")["human_intervention_required"] is False


def test_membership_revocation_rotates_membership_and_key_epochs(tmp_path) -> None:
    control = service(tmp_path)
    join_members(control)
    revoked = control.change_membership(
        tenant_id="tenant-1",
        room_id="room-1",
        action="revoke",
        subject_peer_id="peer-2",
        expected_revision=3,
    )
    assert revoked["epochs"]["membership"] == 4
    assert revoked["epochs"]["key"] == 4
    assert "peer-2" not in revoked["member_ids"]
    offline = control.offline_authority(tenant_id="tenant-1", room_id="room-1", grace_seconds=999)
    assert offline["new_publications_allowed"] is False
    assert offline["route_changes_allowed"] is False
    assert offline["peer_lease_extension_allowed"] is False


def test_tenant_room_and_publication_identifiers_are_scoped_independently(tmp_path) -> None:
    control = service(tmp_path)
    for tenant in ("tenant-1", "tenant-2"):
        control.change_membership(
            tenant_id=tenant,
            room_id="shared-room",
            action="join",
            subject_peer_id="source",
            expected_revision=0,
        )
    assert len(control.overview(room_id="shared-room")["memberships"]) == 2
    assert len(control.overview(tenant_id="tenant-1")["memberships"]) == 1


def test_replanning_monotonically_advances_route_and_topology_epochs(tmp_path) -> None:
    control = service(tmp_path)
    join_members(control)
    first = control.plan_publication(
        tenant_id="tenant-1",
        room_id="room-1",
        publication_id="publication-1",
        source_peer_id="source",
        candidates=candidates(),
    )
    second = control.plan_publication(
        tenant_id="tenant-1",
        room_id="room-1",
        publication_id="publication-1",
        source_peer_id="source",
        candidates=candidates(),
        expected_revision=1,
    )
    assert second["epochs"]["route"] == first["epochs"]["route"] + 1
    assert second["epochs"]["topology"] == first["epochs"]["topology"] + 1


def test_automatic_failover_requires_complaint_quorum_and_issues_backup_ticket(tmp_path) -> None:
    control = service(tmp_path)
    join_members(control)
    plan = control.plan_publication(
        tenant_id="tenant-1",
        room_id="room-1",
        publication_id="publication-1",
        source_peer_id="source",
        candidates=candidates(),
    )
    lease = next(item for item in plan["leases"] if item["backup_parent_id"] is not None)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1_000)
    observations = [
        {
            "observer_peer_id": observer,
            "relay_peer_id": lease["primary_parent_id"],
            "route_epoch": lease["epochs"]["route"],
            "delivery_ratio": 0.5,
            "delay_ms": 4_000,
            "sample_count": 10,
            "observed_at_ms": now_ms,
        }
        for observer in ("peer-1", "peer-2")
    ]
    result = control.request_automatic_failover(
        tenant_id="tenant-1",
        room_id="room-1",
        publication_id="publication-1",
        lease_id=lease["lease_id"],
        observations=observations,
    )
    assert result["switch_to_backup"] is True
    assert result["ticket"]["responder_peer_id"] == lease["backup_parent_id"]
    repeated = control.request_automatic_failover(
        tenant_id="tenant-1",
        room_id="room-1",
        publication_id="publication-1",
        lease_id=lease["lease_id"],
        observations=observations,
    )
    assert repeated["reason_code"] == "peer_overlay_failover_cooldown"
