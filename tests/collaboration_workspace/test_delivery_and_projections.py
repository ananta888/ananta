from __future__ import annotations

from pathlib import Path

import pytest

from agent.services.collaboration_delivery_service import (
    CollaborationDeliveryPolicy,
    CollaborationDeliveryService,
    CollaborationProjectionService,
)
from agent.services.collaboration_workspace_store import (
    CollaborationStoreConflict,
    CollaborationWorkspaceStore,
)
from ananta_contracts.collaboration_workspace import canonical_digest
from tests.collaboration_workspace.helpers import actor, build_event, service


def _workspace_with_event(database: Path, *, key: str = "event-one") -> CollaborationWorkspaceStore:
    workspaces = service(database)
    workspaces.create_workspace(
        tenant_id="tenant-a",
        principal_id="user-a",
        title="Delivery",
        owner=actor(),
        workspace_id="workspace-a",
    )
    workspaces.append_event(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        event=build_event(
            workspace_id="workspace-a",
            actor_binding_id="human-user-a",
            event_type="message.posted",
            payload={"text": key},
            idempotency_key=key,
        ),
    )
    return CollaborationWorkspaceStore(database)


def test_outbox_retry_is_leased_bounded_and_attempt_bound(tmp_path: Path) -> None:
    store = _workspace_with_event(tmp_path / "collaboration.sqlite3")
    current = [100.0]
    delivery = CollaborationDeliveryService(
        store,
        policy=CollaborationDeliveryPolicy(max_attempts=2, base_backoff_seconds=3),
        clock=lambda: current[0],
    )
    first = delivery.claim("tenant-a", consumer_id="consumer-a", lease_seconds=10)[0]
    assert delivery.claim("tenant-a", consumer_id="consumer-b", lease_seconds=10) == []
    retry = delivery.fail(
        "tenant-a",
        first["event_id"],
        first["attempt_id"],
        attempt=first["attempt"],
        error_code="transport_unavailable",
    )
    assert (retry["status"], retry["next_attempt_at"]) == ("retry", 103.0)
    current[0] = 102.9
    assert delivery.claim("tenant-a", consumer_id="consumer-b") == []
    current[0] = 103.0
    second = delivery.claim("tenant-a", consumer_id="consumer-b")[0]
    with pytest.raises(CollaborationStoreConflict, match="attempt_conflict"):
        delivery.complete("tenant-a", first["event_id"], first["attempt_id"])
    terminal = delivery.fail(
        "tenant-a",
        second["event_id"],
        second["attempt_id"],
        attempt=second["attempt"],
        error_code="mapping_rejected",
    )
    assert terminal["status"] == "failed"
    current[0] = 10_000.0
    assert delivery.claim("tenant-a", consumer_id="consumer-c") == []


def test_expired_outbox_lease_is_reclaimed_and_old_attempt_is_fenced(tmp_path: Path) -> None:
    store = _workspace_with_event(tmp_path / "collaboration.sqlite3")
    current = [10.0]
    delivery = CollaborationDeliveryService(store, clock=lambda: current[0])
    first = delivery.claim("tenant-a", consumer_id="consumer-a", lease_seconds=5)[0]
    current[0] = 15.0
    second = delivery.claim("tenant-a", consumer_id="consumer-b", lease_seconds=5)[0]
    assert (first["attempt"], second["attempt"]) == (1, 2)
    assert first["attempt_id"] != second["attempt_id"]
    with pytest.raises(CollaborationStoreConflict, match="attempt_conflict"):
        delivery.complete("tenant-a", first["event_id"], first["attempt_id"])
    assert delivery.complete("tenant-a", second["event_id"], second["attempt_id"])["status"] == "delivered"


def test_inbox_replay_binds_origin_mapping_and_digest(tmp_path: Path) -> None:
    current = [42.0]
    delivery = CollaborationDeliveryService(
        CollaborationWorkspaceStore(tmp_path / "collaboration.sqlite3"),
        clock=lambda: current[0],
    )
    digest = canonical_digest({"content": "event"})
    arguments = {
        "origin": "relay.example",
        "adapter_id": "buzz-primary",
        "external_event_id": "nostr-event-a",
        "mapping_version": "buzz-v1",
        "payload_digest": digest,
    }
    assert delivery.admit_external("tenant-a", **arguments)["replayed"] is False
    current[0] = 99.0
    replay = delivery.admit_external("tenant-a", **arguments)
    assert replay["replayed"] is True
    assert replay["admitted_at"] == 42.0
    for field, value in (
        ("origin", "other-relay.example"),
        ("mapping_version", "buzz-v2"),
        ("payload_digest", canonical_digest({"content": "changed"})),
    ):
        conflicting = {**arguments, field: value}
        with pytest.raises(CollaborationStoreConflict, match="inbox_replay_conflict"):
            delivery.admit_external("tenant-a", **conflicting)


def test_projection_rebuild_is_deterministic_and_detects_drift(tmp_path: Path) -> None:
    database = tmp_path / "collaboration.sqlite3"
    store = _workspace_with_event(database)
    projections = CollaborationProjectionService(store)
    first = projections.rebuild_all("tenant-a", "workspace-a")
    second = projections.rebuild_all("tenant-a", "workspace-a")
    assert {name: value["state_digest"] for name, value in first.items()} == {
        name: value["state_digest"] for name, value in second.items()
    }
    assert projections.verify_all("tenant-a", "workspace-a")["ok"] is True

    workspaces = service(database)
    workspaces.append_event(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        event=build_event(
            workspace_id="workspace-a",
            actor_binding_id="human-user-a",
            event_type="message.posted",
            payload={"text": "event-two"},
            idempotency_key="event-two",
        ),
    )
    drift = projections.verify_all("tenant-a", "workspace-a")
    assert drift["ok"] is False
    assert drift["drifted"] == ["timeline", "search", "threads"]
    projections.rebuild_all("tenant-a", "workspace-a")
    assert projections.verify_all("tenant-a", "workspace-a")["ok"] is True
