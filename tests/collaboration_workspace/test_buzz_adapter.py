from __future__ import annotations

from pathlib import Path

import pytest

from agent.adapters.buzz_collaboration import (
    MAPPING_VERSION,
    PINNED_BUZZ_REVISION,
    BuzzBridgeConfig,
    BuzzBridgeDeliveryStore,
    BuzzCollaborationBridge,
    buzz_bridge_conformance,
)
from agent.services.collaboration_delivery_service import CollaborationDeliveryService
from agent.services.collaboration_workspace_store import CollaborationWorkspaceStore
from ananta_contracts.collaboration_workspace import canonical_digest, canonical_json
from tests.collaboration_workspace.helpers import actor, build_event, service


class Relay:
    def __init__(self) -> None:
        self.published: dict[str, str] = {}

    def negotiate(self):
        return {"mapping_version": MAPPING_VERSION, "revision": PINNED_BUZZ_REVISION}

    def publish(self, envelope, *, idempotency_key: str):
        self.published.setdefault(idempotency_key, f"external-{len(self.published) + 1}")
        return {"external_event_id": self.published[idempotency_key]}


class Keys:
    def sign(self, *, tenant_id: str, workspace_id: str, actor_binding_id: str, payload: bytes) -> str:
        assert (tenant_id, workspace_id, actor_binding_id) == (
            "tenant-a",
            "workspace-a",
            "human-user-a",
        )
        return canonical_digest(payload.hex())


class Signatures:
    def verify(self, *, external_actor_id: str, payload: bytes, signature: str) -> bool:
        return external_actor_id == "npub-a" and signature == canonical_digest(payload.hex())


def _bridge(
    tmp_path: Path,
    relay: Relay | None = None,
    *,
    membership_active=None,
) -> BuzzCollaborationBridge:
    database = tmp_path / "collaboration.sqlite3"
    return BuzzCollaborationBridge(
        BuzzBridgeConfig("tenant-a", "workspace-a", "buzz-a", "relay.example", enabled=True),
        relay=relay or Relay(),
        keys=Keys(),
        signatures=Signatures(),
        deliveries=BuzzBridgeDeliveryStore(tmp_path / "buzz.sqlite3"),
        inbox=CollaborationDeliveryService(CollaborationWorkspaceStore(database)),
        actor_mapping=lambda external: "human-user-a" if external == "npub-a" else None,
        room_mapping=lambda external: "room-main" if external == "buzz-room-a" else None,
        membership_active=membership_active
        or (lambda actor_id, room_id: (actor_id, room_id) == ("human-user-a", "room-main")),
        clock=lambda: 100.0,
    )


def _inbound(*, kind: str = "buzz.message.v1"):
    payload = {"text": "external proposal"}
    unsigned = {
        "external_event_id": "external-a",
        "mapping_version": MAPPING_VERSION,
        "kind": kind,
        "external_actor_id": "npub-a",
        "external_room_id": "buzz-room-a",
        "payload": payload,
        "payload_digest": canonical_digest(payload),
        "origin_adapter_id": "buzz-remote",
        "hop_count": 0,
    }
    return {**unsigned, "signature": canonical_digest(canonical_json(unsigned).encode().hex())}


def test_connector_is_default_off_secret_safe_and_capability_pinned(tmp_path: Path) -> None:
    disabled = BuzzBridgeConfig("tenant-a", "workspace-a", "buzz-a", "relay.example")
    bridge = BuzzCollaborationBridge(
        disabled,
        relay=Relay(),
        keys=Keys(),
        signatures=Signatures(),
        deliveries=BuzzBridgeDeliveryStore(tmp_path / "buzz.sqlite3"),
        inbox=CollaborationDeliveryService(CollaborationWorkspaceStore(tmp_path / "collaboration.sqlite3")),
        actor_mapping=lambda _value: None,
        room_mapping=lambda _value: None,
        membership_active=lambda _actor, _room: False,
    )
    assert bridge.connect() == {"connected": False, "reason_code": "buzz_bridge_disabled"}
    assert bridge.capabilities["native_core_available"] is True
    with pytest.raises(ValueError, match="config_invalid"):
        BuzzBridgeConfig("tenant-a", "workspace-a", "buzz-a", "user:secret@relay.example", enabled=True)


def test_outbound_delivery_is_allowlisted_redacted_persisted_and_idempotent(tmp_path: Path) -> None:
    relay = Relay()
    bridge = _bridge(tmp_path, relay)
    assert bridge.connect()["connected"] is True
    event = build_event(
        workspace_id="workspace-a",
        room_id="room-main",
        actor_binding_id="human-user-a",
        event_type="message.posted",
        payload={"text": "safe export"},
        idempotency_key="message-a",
    )
    first = bridge.deliver(event)
    replay = bridge.deliver(event)
    assert (first["status"], replay["replayed"], len(relay.published)) == ("delivered", True, 1)
    restricted = {**event, "event_id": "event-restricted", "visibility": "restricted"}
    with pytest.raises(ValueError, match="export_forbidden"):
        bridge.deliver(restricted)
    sensitive = build_event(
        workspace_id="workspace-a",
        actor_binding_id="human-user-a",
        event_type="message.posted",
        payload={"api_token": "secret"},
        idempotency_key="sensitive",
    )
    with pytest.raises(ValueError, match="sensitive_content_rejected"):
        bridge.deliver(sensitive)


def test_two_reconnect_replay_cycles_create_one_logical_inbound_proposal(tmp_path: Path) -> None:
    envelope = _inbound()
    first = _bridge(tmp_path)
    assert first.connect()["connected"] is True
    proposal = first.propose(envelope)
    first.disconnect()
    second = _bridge(tmp_path)
    second.connect()
    replay = second.propose(envelope)
    second.disconnect()
    third = _bridge(tmp_path)
    third.connect()
    replay_again = third.propose(envelope)
    assert proposal["replayed"] is False
    assert replay["replayed"] is True
    assert replay_again["replayed"] is True
    assert replay_again["authority_granted"] is False


def test_inbound_command_and_unknown_kind_never_grant_authority(tmp_path: Path) -> None:
    bridge = _bridge(tmp_path)
    bridge.connect()
    command = bridge.propose(_inbound(kind="buzz.command-intent.v1"))
    assert command["proposal_type"] == "hub_command_intent_required"
    assert command["authority_granted"] is False
    with pytest.raises(ValueError, match="kind_unsupported"):
        bridge.propose({**_inbound(), "external_event_id": "external-unknown", "kind": "buzz.unknown.v1"})


def test_missing_runtime_evidence_blocks_only_buzz_release(tmp_path: Path) -> None:
    gate = buzz_bridge_conformance(runtime_evidence_verified=False)
    assert gate["local_conformance"] == "passed"
    assert gate["runtime_evidence"] == "unverified"
    assert gate["release_allowed"] is False
    assert gate["native_core_available"] is True
    assert gate["native_core_gate_affected"] is False


def test_outbound_delivery_revalidates_current_membership(tmp_path: Path) -> None:
    active = True
    bridge = _bridge(
        tmp_path,
        membership_active=lambda _actor, _room: active,
    )
    assert bridge.connect()["connected"] is True
    active = False

    with pytest.raises(PermissionError, match="export_membership_stale"):
        bridge.deliver(
            build_event(
                workspace_id="workspace-a",
                room_id="room-main",
                actor_binding_id="human-user-a",
                event_type="message.posted",
                payload={"text": "must be revalidated"},
                idempotency_key="membership-revalidation",
            )
        )

    database = tmp_path / "native.sqlite3"
    native = service(database)
    native.create_workspace(
        tenant_id="tenant-a",
        principal_id="user-a",
        title="Native remains available",
        owner=actor(),
        workspace_id="workspace-a",
    )
    assert native.list_workspaces(tenant_id="tenant-a", principal_actor_id="human-user-a")["items"]
