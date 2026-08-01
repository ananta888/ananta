from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from sqlmodel import Session

from agent.database import engine
from agent.repositories.projects import ProjectRepository


class ProjectCapability(str, Enum):
    READ = "read"
    WRITE = "write"
    MANAGE = "manage"
    MANAGE_MEMBERS = "manage_members"
    ARCHIVE = "archive"


@dataclass(frozen=True)
class AuthorizedProjectScope:
    tenant_id: str
    project_id: str
    team_id: str | None
    subject_id: str
    role: str
    status: str
    capability: ProjectCapability
    lock_version: int


class ProjectAccessError(RuntimeError):
    def __init__(
        self,
        *,
        reason_code: str,
        public_status: int,
        tenant_id: str,
        project_id: str,
    ) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.public_status = public_status
        self.tenant_id = tenant_id
        self.project_id = project_id


class ProjectNotFoundError(ProjectAccessError):
    def __init__(self, *, tenant_id: str, project_id: str) -> None:
        super().__init__(
            reason_code="project_not_found",
            public_status=404,
            tenant_id=tenant_id,
            project_id=project_id,
        )


class ProjectAccessDeniedError(ProjectAccessError):
    def __init__(self, *, tenant_id: str, project_id: str) -> None:
        super().__init__(
            reason_code="project_access_denied",
            public_status=403,
            tenant_id=tenant_id,
            project_id=project_id,
        )


class ProjectArchivedError(ProjectAccessError):
    def __init__(self, *, tenant_id: str, project_id: str) -> None:
        super().__init__(
            reason_code="project_archived",
            public_status=409,
            tenant_id=tenant_id,
            project_id=project_id,
        )


@runtime_checkable
class ProjectAccessPort(Protocol):
    def require(
        self,
        *,
        tenant_id: str,
        project_id: str,
        subject_id: str,
        capability: ProjectCapability,
        tenant_admin: bool = False,
        include_archived: bool = False,
    ) -> AuthorizedProjectScope: ...


SessionFactory = Callable[[], Session]


def _default_session_factory() -> Session:
    return Session(engine)


_ROLE_CAPABILITIES: dict[str, frozenset[ProjectCapability]] = {
    "viewer": frozenset({ProjectCapability.READ}),
    "maintainer": frozenset({ProjectCapability.READ, ProjectCapability.WRITE}),
    "owner": frozenset(ProjectCapability),
    "tenant_admin": frozenset(ProjectCapability),
}


class SqlProjectAccessAuthority:
    """Authorize a project scope without coupling callers to SQLModel."""

    def __init__(self, *, session_factory: SessionFactory | None = None) -> None:
        self._session_factory = session_factory or _default_session_factory

    def require(
        self,
        *,
        tenant_id: str,
        project_id: str,
        subject_id: str,
        capability: ProjectCapability,
        tenant_admin: bool = False,
        include_archived: bool = False,
    ) -> AuthorizedProjectScope:
        try:
            requested_capability = ProjectCapability(capability)
        except ValueError as exc:
            raise ProjectAccessDeniedError(
                tenant_id=tenant_id,
                project_id=project_id,
            ) from exc

        with self._session_factory() as session:
            repository = ProjectRepository(session)
            project = repository.get(tenant_id, project_id)
            if project is None:
                raise ProjectNotFoundError(tenant_id=tenant_id, project_id=project_id)

            if tenant_admin:
                role = "tenant_admin"
            else:
                membership = repository.get_membership(tenant_id, project_id, subject_id)
                if membership is None or membership.state != "active":
                    raise ProjectNotFoundError(tenant_id=tenant_id, project_id=project_id)
                role = membership.role

            if requested_capability not in _ROLE_CAPABILITIES.get(role, frozenset()):
                raise ProjectAccessDeniedError(tenant_id=tenant_id, project_id=project_id)
            if project.status == "archived" and not include_archived:
                raise ProjectArchivedError(tenant_id=tenant_id, project_id=project_id)

            return AuthorizedProjectScope(
                tenant_id=project.tenant_id,
                project_id=project.project_id,
                team_id=project.team_id,
                subject_id=subject_id,
                role=role,
                status=project.status,
                capability=requested_capability,
                lock_version=project.lock_version,
            )
