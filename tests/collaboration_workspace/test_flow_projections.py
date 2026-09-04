from __future__ import annotations

from pathlib import Path

import pytest

from agent.services.collaboration_flow_projection_service import CollaborationFlowProjectionService
from agent.services.collaboration_workspace_store import CollaborationWorkspaceStore
from ananta_contracts.collaboration_workspace import canonical_digest
from tests.collaboration_workspace.helpers import actor, build_event, room, service


class EventStore:
    def __init__(self, events):
        self.events = events

    def projection_events(self, tenant_id: str, workspace_id: str):
        assert (tenant_id, workspace_id) == ("tenant-a", "workspace-a")
        return self.events


def _event(sequence: int, event_type: str, payload: dict, *, grounded: bool = True):
    return {
        "event_id": f"event-{sequence}",
        "sequence": sequence,
        "event_type": event_type,
        "payload": payload,
        "source_refs": ["SRC_registered"] if grounded else [],
        "run_refs": ["RUN_registered"] if grounded else [],
    }


def test_reordered_git_delivery_updates_projection_without_rewriting_history() -> None:
    events = [
        _event(
            1,
            "git.projected",
            {"ref_id": "branch-main", "ref_revision": 2, "head_sha": "b" * 40, "change": "update"},
        ),
        _event(
            2,
            "git.projected",
            {"ref_id": "branch-main", "ref_revision": 1, "head_sha": "a" * 40, "change": "create"},
        ),
        _event(
            3,
            "git.projected",
            {"ref_id": "branch-main", "ref_revision": 3, "head_sha": "c" * 40, "change": "force_push"},
        ),
    ]
    projection = CollaborationFlowProjectionService(EventStore(events)).rebuild(  # type: ignore[arg-type]
        "tenant-a", "workspace-a"
    )
    ref = projection["state"]["git_refs"]["branch-main"]
    assert (ref["ref_revision"], ref["head_sha"], ref["history_discontinuity"]) == (3, "c" * 40, True)
    assert projection["checkpoint"] == 3
    assert projection["writes_authoritative_state"] is False


def test_task_workflow_and_review_views_only_accept_grounded_admitted_events() -> None:
    subject = canonical_digest({"patch": "a"})
    events = [
        _event(1, "task.projected", {"task_id": "task-a", "state": "done"}),
        _event(2, "workflow.projected", {"workflow_id": "workflow-a", "state": "succeeded"}),
        _event(3, "review.recorded", {"subject_digest": subject, "state": "approved"}),
    ]
    service = CollaborationFlowProjectionService(EventStore(events))  # type: ignore[arg-type]
    result = service.rebuild("tenant-a", "workspace-a")
    assert result["state"]["tasks"]["task-a"]["verification_status"] == "hub_verified"
    assert result["state"]["workflows"]["workflow-a"]["verification_status"] == "hub_verified"
    assert result["state"]["reviews"][subject]["verification_status"] == "hub_verified"
    assert result["state"]["artifacts"] == {}

    with pytest.raises(ValueError, match="projection_grounding_missing"):
        CollaborationFlowProjectionService(  # type: ignore[arg-type]
            EventStore([_event(1, "task.projected", {"task_id": "task-a"}, grounded=False)])
        ).rebuild("tenant-a", "workspace-a")


def test_release_notes_are_proposals_and_only_use_explicitly_visible_events() -> None:
    events = [
        _event(1, "task.projected", {"task_id": "task-a", "summary": "Completed feature"}),
        _event(2, "git.projected", {"ref_id": "main", "summary": "Private change"}),
    ]
    notes = CollaborationFlowProjectionService(EventStore(events)).propose_release_notes(  # type: ignore[arg-type]
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        visible_event_ids={"event-1"},
    )
    assert [item["event_id"] for item in notes["entries"]] == ["event-1"]
    assert notes["published"] is False
    assert notes["requires_hub_release_workflow"] is True


def test_flow_projection_filters_restricted_rooms_for_requesting_actor(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    workspaces = service(database)
    workspaces.create_workspace(
        tenant_id="tenant-a", principal_id="user-a", title="Scoped", owner=actor(), workspace_id="workspace-a"
    )
    workspaces.create_room(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        room=room("room-private"),
    )
    workspaces.put_membership(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        actor=actor("human-user-b", subject="user-b"),
        role="member",
        status="active",
    )
    workspaces.put_room_access(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        room_id="room-private",
        access_mode="restricted",
        actor_binding_ids=[],
        expected_revision=1,
    )
    payload = {
        "artifact_id": "artifact-secret",
        "digest": "a" * 64,
        "size_bytes": 10,
        "media_type": "text/plain",
        "scan_status": "clean",
        "export_allowed": False,
    }
    workspaces.append_event(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        event=build_event(
            workspace_id="workspace-a",
            room_id="room-private",
            actor_binding_id="human-user-a",
            event_type="artifact.linked",
            payload=payload,
            idempotency_key="private-artifact",
        ),
    )
    projection = CollaborationFlowProjectionService(CollaborationWorkspaceStore(database))
    owner = projection.rebuild("tenant-a", "workspace-a", principal_actor_id="human-user-a")
    member = projection.rebuild("tenant-a", "workspace-a", principal_actor_id="human-user-b")
    assert "artifact-secret" in owner["state"]["artifacts"]
    assert member["state"]["artifacts"] == {}
