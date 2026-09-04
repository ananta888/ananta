from __future__ import annotations

from pathlib import Path

import pytest

from agent.services.collaboration_binding_service import CollaborationBindingService
from agent.services.collaboration_workspace_policy import CollaborationWorkspacePolicy
from agent.services.collaboration_workspace_store import CollaborationWorkspaceStore
from tests.collaboration_workspace.helpers import actor, service


class BindingAuthority:
    def __init__(self, *, verified: bool = True) -> None:
        self.verified = verified
        self.calls = 0

    def verify(self, *, tenant_id: str, principal_actor_id: str, binding):
        self.calls += 1
        assert (tenant_id, principal_actor_id) == ("tenant-a", "human-user-a")
        return {
            "verified": self.verified,
            "reason_code": "binding_verified" if self.verified else "binding_not_found",
            "authoritative_revision": binding["revision"],
        }


def _binding(*, change: str = "create", head: str | None = None):
    return {
        "binding_kind": "branch",
        "binding_id": "repo-a:feature-collaboration",
        "project_id": "project-a",
        "lifecycle": "active",
        "revision": "revision-a",
        "metadata": {
            "repository_id": "repo-a",
            "remote": "origin",
            "branch_ref": "feature/collaboration",
            "base_ref": "main",
            "head_sha": head or "a" * 40,
            "fork_id": None,
            "change": change,
        },
    }


def _service(database: Path, authority: BindingAuthority):
    workspaces = service(database)
    workspaces.create_workspace(
        tenant_id="tenant-a",
        principal_id="user-a",
        title="Bindings",
        owner=actor(),
        workspace_id="workspace-a",
    )
    return CollaborationBindingService(
        CollaborationWorkspaceStore(database),
        policy=CollaborationWorkspacePolicy(),
        authority=authority,
    )


def test_branch_room_creation_is_normalized_authorized_and_idempotent(tmp_path: Path) -> None:
    authority = BindingAuthority()
    bindings = _service(tmp_path / "state.sqlite3", authority)
    first = bindings.create_branch_room(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        title="Feature branch",
        binding=_binding(),
    )
    replay = bindings.create_branch_room(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        title="Feature branch",
        binding=_binding(),
    )
    assert first["binding_kind"] == "branch"
    assert first["metadata"]["branch_ref"] == "feature/collaboration"
    assert replay["room_id"] == first["room_id"]
    assert replay["replayed"] is True


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("branch_ref", "../secret", "git_ref_invalid"),
        ("head_sha", "not-a-sha", "git_head_invalid"),
        ("change", "rewrite_history", "git_change_invalid"),
    ],
)
def test_invalid_git_binding_fails_closed(tmp_path: Path, field: str, value: str, reason: str) -> None:
    bindings = _service(tmp_path / "state.sqlite3", BindingAuthority())
    binding = _binding()
    binding["metadata"][field] = value
    with pytest.raises(ValueError, match=reason):
        bindings.create_branch_room(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            principal_actor_id="human-user-a",
            title="Invalid",
            binding=binding,
        )


def test_denied_binding_does_not_leave_orphan_room(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    bindings = _service(database, BindingAuthority(verified=False))
    with pytest.raises(PermissionError, match="binding_not_found"):
        bindings.create_branch_room(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            principal_actor_id="human-user-a",
            title="Denied",
            binding=_binding(),
        )
    workspace = service(database).get_workspace(
        tenant_id="tenant-a", workspace_id="workspace-a", principal_actor_id="human-user-a"
    )
    assert workspace["rooms"] == []
