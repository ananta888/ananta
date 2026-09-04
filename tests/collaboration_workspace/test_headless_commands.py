from __future__ import annotations

from pathlib import Path

import pytest

from agent.services.collaboration_command_service import (
    CollaborationCommandService,
    PreauthorizedCommandPolicy,
)
from agent.services.collaboration_workspace_policy import CollaborationWorkspacePolicy
from agent.services.collaboration_workspace_store import (
    CollaborationStoreConflict,
    CollaborationWorkspaceStore,
)
from ananta_contracts.collaboration_workspace import canonical_digest
from tests.collaboration_workspace.helpers import actor, room, service


def _command_service(database: Path, tools: frozenset[str]) -> CollaborationCommandService:
    workspaces = service(database)
    workspaces.create_workspace(
        tenant_id="tenant-a",
        principal_id="user-a",
        title="Commands",
        owner=actor(),
        workspace_id="workspace-a",
    )
    workspaces.create_room(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        room=room(),
    )
    return CollaborationCommandService(
        CollaborationWorkspaceStore(database),
        workspace_policy=CollaborationWorkspacePolicy(),
        command_policy=PreauthorizedCommandPolicy(allowed_tool_ids=tools, revision=7),
    )


def _request(*, request_id: str = "request-a", tool_id: str = "pytest") -> dict[str, object]:
    return {
        "request_id": request_id,
        "workspace_id": "workspace-a",
        "room_id": "room-main",
        "actor_binding_id": "human-user-a",
        "task_id": "task-a",
        "tool_id": tool_id,
        "operation": "execute",
        "plan_digest": canonical_digest({"command": "pytest -q"}),
        "artifact_digest": None,
        "policy_revision": 7,
    }


def test_headless_policy_approves_or_blocks_without_waiting(tmp_path: Path) -> None:
    commands = _command_service(tmp_path / "state.sqlite3", frozenset({"pytest"}))
    approved = commands.decide(tenant_id="tenant-a", principal_actor_id="human-user-a", request=_request())
    blocked = commands.decide(
        tenant_id="tenant-a",
        principal_actor_id="human-user-a",
        request=_request(request_id="request-b", tool_id="shell"),
    )
    assert (approved["state"], approved["human_intervention_required"], approved["terminal"]) == (
        "approved",
        False,
        True,
    )
    assert (blocked["state"], blocked["reason_code"], blocked["terminal"]) == (
        "blocked",
        "command_policy_blocked",
        True,
    )


def test_command_decision_is_exactly_bound_and_replay_safe(tmp_path: Path) -> None:
    commands = _command_service(tmp_path / "state.sqlite3", frozenset({"pytest"}))
    request = _request()
    first = commands.decide(tenant_id="tenant-a", principal_actor_id="human-user-a", request=request)
    replay = commands.decide(tenant_id="tenant-a", principal_actor_id="human-user-a", request=request)
    assert (first["replayed"], replay["replayed"]) == (False, True)
    changed = {**request, "plan_digest": canonical_digest({"command": "different"})}
    with pytest.raises(CollaborationStoreConflict, match="replay_conflict"):
        commands.decide(tenant_id="tenant-a", principal_actor_id="human-user-a", request=changed)


def test_stale_policy_revision_fails_bounded(tmp_path: Path) -> None:
    commands = _command_service(tmp_path / "state.sqlite3", frozenset({"pytest"}))
    stale = {**_request(), "policy_revision": 6}
    with pytest.raises(PermissionError, match="policy_revision_stale"):
        commands.decide(tenant_id="tenant-a", principal_actor_id="human-user-a", request=stale)
