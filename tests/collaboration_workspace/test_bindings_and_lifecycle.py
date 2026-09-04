from __future__ import annotations

from pathlib import Path

import pytest

from agent.services.collaboration_binding_service import CollaborationBindingService
from agent.services.collaboration_domain_binding_authority import (
    CollaborationDomainRecord,
    HubCollaborationBindingAuthority,
)
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


class DomainCatalog:
    def __init__(self, record: CollaborationDomainRecord | None) -> None:
        self.record = record
        self.access = True

    def principal_can_access(self, *, tenant_id: str, project_id: str, subject_id: str) -> bool:
        assert (tenant_id, project_id, subject_id) == ("tenant-a", "project-a", "user-a")
        return self.access

    def resolve(self, *, tenant_id: str, project_id: str, kind: str, object_id: str):
        assert (tenant_id, project_id, kind, object_id) == ("tenant-a", "project-a", "task", "task-a")
        return self.record


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


def test_hub_domain_authority_checks_principal_scope_lifecycle_and_revision(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    _service(database, BindingAuthority())
    catalog = DomainCatalog(CollaborationDomainRecord("task", "task-a", "project-a", "active", "7"))
    authority = HubCollaborationBindingAuthority(CollaborationWorkspaceStore(database), catalog)
    binding = {
        "binding_kind": "task",
        "binding_id": "task-a",
        "project_id": "project-a",
        "lifecycle": "active",
        "revision": "7",
        "metadata": {},
    }
    assert authority.verify(tenant_id="tenant-a", principal_actor_id="human-user-a", binding=binding) == {
        "verified": True,
        "reason_code": "collaboration_binding_verified",
        "authoritative_revision": "7",
    }
    stale = authority.verify(
        tenant_id="tenant-a", principal_actor_id="human-user-a", binding={**binding, "revision": "6"}
    )
    assert stale == {
        "verified": False,
        "reason_code": "collaboration_binding_revision_stale",
        "authoritative_revision": "7",
    }
    catalog.access = False
    denied = authority.verify(tenant_id="tenant-a", principal_actor_id="human-user-a", binding=binding)
    assert denied["reason_code"] == "collaboration_binding_project_access_denied"
