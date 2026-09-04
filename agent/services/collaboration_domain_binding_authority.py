"""Hub-owned authority for collaboration bindings to domain objects."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from sqlmodel import Session, select

from agent.db_models.organizations import OrganizationInstanceDB
from agent.db_models.planning import GoalDB
from agent.db_models.projects import ProjectDB, ProjectMembershipDB
from agent.db_models.tasks import ArchivedTaskDB, TaskDB
from agent.services.collaboration_workspace_store import CollaborationWorkspaceStore


@dataclass(frozen=True, slots=True)
class CollaborationDomainRecord:
    kind: str
    object_id: str
    project_id: str
    lifecycle: str
    revision: str


class CollaborationDomainCatalog(Protocol):
    def principal_can_access(self, *, tenant_id: str, project_id: str, subject_id: str) -> bool: ...

    def resolve(
        self, *, tenant_id: str, project_id: str, kind: str, object_id: str
    ) -> CollaborationDomainRecord | None: ...


class SqlModelCollaborationDomainCatalog:
    """Read-only adapter over authoritative Hub domain tables."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._sessions = session_factory

    def principal_can_access(self, *, tenant_id: str, project_id: str, subject_id: str) -> bool:
        with self._sessions() as session:
            project = session.get(ProjectDB, (tenant_id, project_id))
            membership = session.get(ProjectMembershipDB, (tenant_id, project_id, subject_id))
            return bool(project and membership and membership.state == "active")

    def resolve(
        self, *, tenant_id: str, project_id: str, kind: str, object_id: str
    ) -> CollaborationDomainRecord | None:
        with self._sessions() as session:
            if kind == "project":
                row = session.get(ProjectDB, (tenant_id, project_id))
                if row is None or row.project_id != object_id:
                    return None
                return CollaborationDomainRecord(
                    kind, row.project_id, row.project_id, row.status, str(row.lock_version)
                )
            if kind == "organization":
                row = session.exec(
                    select(OrganizationInstanceDB).where(
                        OrganizationInstanceDB.tenant_id == tenant_id,
                        OrganizationInstanceDB.project_id == project_id,
                        OrganizationInstanceDB.organization_id == object_id,
                    )
                ).first()
                if row is None:
                    return None
                return CollaborationDomainRecord(
                    kind, row.organization_id, row.project_id, row.lifecycle, str(row.lock_version)
                )
            if kind == "goal":
                row = session.exec(
                    select(GoalDB).where(
                        GoalDB.tenant_id == tenant_id,
                        GoalDB.project_id == project_id,
                        GoalDB.id == object_id,
                    )
                ).first()
                if row is None:
                    return None
                return CollaborationDomainRecord(
                    kind, row.id, str(row.project_id), _goal_lifecycle(row.status), _epoch_revision(row.updated_at)
                )
            if kind == "task":
                row = session.exec(
                    select(TaskDB).where(
                        TaskDB.tenant_id == tenant_id,
                        TaskDB.project_id == project_id,
                        TaskDB.id == object_id,
                    )
                ).first()
                if row is not None:
                    return CollaborationDomainRecord(
                        kind, row.id, str(row.project_id), _task_lifecycle(row.status), str(row.kanban_revision)
                    )
                archived = session.exec(
                    select(ArchivedTaskDB).where(
                        ArchivedTaskDB.tenant_id == tenant_id,
                        ArchivedTaskDB.project_id == project_id,
                        ArchivedTaskDB.id == object_id,
                    )
                ).first()
                if archived is not None:
                    return CollaborationDomainRecord(
                        kind, archived.id, str(archived.project_id), "archived", str(archived.kanban_revision)
                    )
        return None


class HubCollaborationBindingAuthority:
    """Verifies requested bindings against actor identity and Hub state."""

    def __init__(self, store: CollaborationWorkspaceStore, catalog: CollaborationDomainCatalog) -> None:
        self._store = store
        self._catalog = catalog

    def verify(self, *, tenant_id: str, principal_actor_id: str, binding: Mapping[str, Any]) -> Mapping[str, Any]:
        actor = self._store.actor(tenant_id, principal_actor_id)
        subject = str((actor or {}).get("authority_subject") or "").strip()
        project_id = str(binding.get("project_id") or "").strip()
        if not subject:
            return _denied("collaboration_binding_actor_unknown")
        if not self._catalog.principal_can_access(tenant_id=tenant_id, project_id=project_id, subject_id=subject):
            return _denied("collaboration_binding_project_access_denied")
        kind = str(binding.get("binding_kind") or "")
        if kind == "branch":
            return _denied("collaboration_branch_authority_not_configured")
        record = self._catalog.resolve(
            tenant_id=tenant_id,
            project_id=project_id,
            kind=kind,
            object_id=str(binding.get("binding_id") or ""),
        )
        if record is None:
            return _denied("collaboration_binding_not_found")
        if binding.get("lifecycle") != record.lifecycle:
            return _denied("collaboration_binding_lifecycle_stale", record.revision)
        if str(binding.get("revision") or "") != record.revision:
            return _denied("collaboration_binding_revision_stale", record.revision)
        return {
            "verified": True,
            "reason_code": "collaboration_binding_verified",
            "authoritative_revision": record.revision,
        }


def _denied(reason_code: str, revision: str = "unavailable") -> dict[str, Any]:
    return {"verified": False, "reason_code": reason_code, "authoritative_revision": revision}


def _epoch_revision(value: float) -> str:
    return str(int(float(value) * 1_000_000))


def _goal_lifecycle(status: str) -> str:
    return "archived" if str(status).strip().lower() in {"archived", "cancelled", "completed", "failed"} else "active"


def _task_lifecycle(status: str) -> str:
    return (
        "archived"
        if str(status).strip().lower() in {"archived", "cancelled", "completed", "done", "failed"}
        else "active"
    )


__all__ = [
    "CollaborationDomainCatalog",
    "CollaborationDomainRecord",
    "HubCollaborationBindingAuthority",
    "SqlModelCollaborationDomainCatalog",
]
