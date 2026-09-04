from __future__ import annotations

from pathlib import Path

import pytest

from agent.ports.evidence_identity import EvidenceBindingVerification
from agent.services.collaboration_evidence_policy import CollaborationEvidencePolicy
from agent.services.collaboration_workspace_policy import CollaborationWorkspacePolicy
from agent.services.collaboration_workspace_service import CollaborationWorkspaceService
from agent.services.collaboration_workspace_store import CollaborationWorkspaceStore
from tests.collaboration_workspace.helpers import actor, build_event


class Registry:
    def __init__(self, *, verified: bool) -> None:
        self.verified = verified
        self.calls: list[dict[str, object]] = []

    def verify_release_binding(self, **values):
        self.calls.append(values)
        return EvidenceBindingVerification(
            verified=self.verified,
            reason_code="verified" if self.verified else "evidence_run_identity_not_found",
            source_ids=tuple(values["source_ids"]),
            run_id=values["run_id"],
            evidence_scope=values["required_scope"] if self.verified else None,
        )


def _service(database: Path, registry: Registry | None) -> CollaborationWorkspaceService:
    value = CollaborationWorkspaceService(
        CollaborationWorkspaceStore(database),
        policy=CollaborationWorkspacePolicy(),
        evidence_policy=CollaborationEvidencePolicy(registry),  # type: ignore[arg-type]
    )
    value.create_workspace(
        tenant_id="tenant-a",
        principal_id="user-a",
        title="Evidence",
        owner=actor(),
        workspace_id="workspace-a",
    )
    return value


def _grounded_event() -> dict[str, object]:
    event = build_event(
        workspace_id="workspace-a",
        actor_binding_id="human-user-a",
        event_type="task.projected",
        payload={
            "project_id": "project-a",
            "task_id": "task-a",
            "repository_revision": "a" * 40,
            "evidence_scope": "production",
            "state": "done",
        },
        idempotency_key="grounded-task",
    )
    event["source_refs"] = ["SRC_registered"]
    event["run_refs"] = ["RUN_registered"]
    return event


def test_grounded_event_requires_configured_hub_registry(tmp_path: Path) -> None:
    workspaces = _service(tmp_path / "state.sqlite3", None)
    with pytest.raises(PermissionError, match="hub_evidence_registry_required"):
        workspaces.append_event(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            principal_actor_id="human-user-a",
            event=_grounded_event(),
        )


def test_unknown_registry_identity_is_unverified_and_not_persisted(tmp_path: Path) -> None:
    registry = Registry(verified=False)
    workspaces = _service(tmp_path / "state.sqlite3", registry)
    with pytest.raises(PermissionError, match="evidence_unverified:evidence_run_identity_not_found"):
        workspaces.append_event(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            principal_actor_id="human-user-a",
            event=_grounded_event(),
        )
    assert len(registry.calls) == 1
    assert registry.calls[0]["tenant_id"] == "tenant-a"


def test_registered_immutable_binding_can_create_grounded_projection(tmp_path: Path) -> None:
    registry = Registry(verified=True)
    workspaces = _service(tmp_path / "state.sqlite3", registry)
    appended = workspaces.append_event(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        event=_grounded_event(),
    )
    assert appended["event_type"] == "task.projected"
    assert registry.calls[0] == {
        "tenant_id": "tenant-a",
        "project_id": "project-a",
        "run_id": "RUN_registered",
        "required_scope": "production",
        "task_id": "task-a",
        "repository_revision": "a" * 40,
        "source_ids": ("SRC_registered",),
    }
