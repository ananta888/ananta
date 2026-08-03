"""Authorization boundary for generic Task read models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, NoReturn, Protocol

from agent.services.organization_membership_service import (
    OrganizationAccessPrincipal,
)
from agent.services.project_access_authority import (
    ProjectAccessError,
    ProjectCapability,
)
from agent.services.source_control_access_policy import HubSourcePrincipal


class TaskReadProjectAccessPort(Protocol):
    def require(
        self,
        *,
        tenant_id: str,
        project_id: str,
        subject_id: str,
        capability: ProjectCapability,
        tenant_admin: bool = False,
        include_archived: bool = False,
    ) -> Any: ...


class TaskReadOrganizationMembershipPort(Protocol):
    def can_view(
        self,
        *,
        principal: OrganizationAccessPrincipal,
        tenant_id: str,
        project_id: str,
        organization_id: str,
    ) -> bool: ...


class TaskReadAccessError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int) -> None:
        super().__init__(reason_code)
        self.reason_code = str(reason_code)
        self.status_code = int(status_code)


class TaskReadAccessService:
    """Authorize one generic Task read without revealing foreign identities.

    Scoped Tasks require the exact authenticated tenant/project, current
    project READ authority, current Organization membership when applicable,
    and either Task ownership or project-owner authority. Administrators are
    the compatibility authority only for fully unscoped legacy Tasks. Scoped
    administrator reads still require the Project READ and Organization
    membership boundaries, using tenant-admin Project semantics.
    """

    def require(
        self,
        *,
        task: Mapping[str, Any],
        principal: HubSourcePrincipal,
        project_access: TaskReadProjectAccessPort | None,
        organization_membership: TaskReadOrganizationMembershipPort | None,
    ) -> None:
        tenant_id = str(task.get("tenant_id") or "").strip()
        project_id = str(task.get("project_id") or "").strip()

        if not tenant_id and not project_id:
            if principal.is_admin:
                return
            self._hide()
        if not tenant_id or not project_id:
            self._hide()

        if principal.tenant_id and principal.tenant_id != tenant_id:
            self._hide()
        if principal.project_id and principal.project_id != project_id:
            self._hide()

        if not principal.is_admin and (
            principal.tenant_id != tenant_id
            or principal.project_id != project_id
        ):
            self._hide()
        if project_access is None:
            raise TaskReadAccessError(
                "project_access_authority_unavailable",
                status_code=503,
            )
        try:
            authorized_scope = project_access.require(
                tenant_id=tenant_id,
                project_id=project_id,
                subject_id=principal.subject_id,
                capability=ProjectCapability.READ,
                tenant_admin=principal.is_admin,
            )
        except ProjectAccessError:
            self._hide()

        organization_id = str(task.get("organization_id") or "").strip()
        if organization_id:
            if organization_membership is None:
                raise TaskReadAccessError(
                    "organization_membership_service_unavailable",
                    status_code=503,
                )
            if not organization_membership.can_view(
                principal=OrganizationAccessPrincipal(
                    principal_id=principal.subject_id,
                    tenant_id=tenant_id,
                    project_id=project_id,
                ),
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization_id,
            ):
                self._hide()

        if principal.is_admin:
            return
        role = str(getattr(authorized_scope, "role", "") or "").strip()
        if role in {"owner", "tenant_admin"}:
            return
        if self.task_owner(task) != principal.subject_id:
            self._hide()

    def can_read(
        self,
        *,
        task: Mapping[str, Any],
        principal: HubSourcePrincipal,
        project_access: TaskReadProjectAccessPort | None,
        organization_membership: TaskReadOrganizationMembershipPort | None,
    ) -> bool:
        try:
            self.require(
                task=task,
                principal=principal,
                project_access=project_access,
                organization_membership=organization_membership,
            )
        except TaskReadAccessError as exc:
            if exc.status_code == 404:
                return False
            raise
        return True

    @staticmethod
    def task_owner(task: Mapping[str, Any]) -> str:
        events = [
            event
            for event in list(task.get("history") or [])
            if isinstance(event, Mapping)
            and str(event.get("event_type") or "") == "task_ingested"
        ]
        if len(events) != 1:
            return ""
        return str(events[0].get("actor") or "").strip()

    @staticmethod
    def _hide() -> NoReturn:
        raise TaskReadAccessError(
            "task_read_not_found",
            status_code=404,
        )


@dataclass(frozen=True, slots=True)
class TaskReadAccessContext:
    principal: HubSourcePrincipal
    project_access: TaskReadProjectAccessPort | None
    organization_membership: TaskReadOrganizationMembershipPort | None
    service: TaskReadAccessService

    def require(self, task: Mapping[str, Any]) -> None:
        self.service.require(
            task=task,
            principal=self.principal,
            project_access=self.project_access,
            organization_membership=self.organization_membership,
        )

    def can_read(self, task: Mapping[str, Any]) -> bool:
        return self.service.can_read(
            task=task,
            principal=self.principal,
            project_access=self.project_access,
            organization_membership=self.organization_membership,
        )


_SERVICE = TaskReadAccessService()


def get_task_read_access_service() -> TaskReadAccessService:
    return _SERVICE


__all__ = [
    "TaskReadAccessContext",
    "TaskReadAccessError",
    "TaskReadAccessService",
    "get_task_read_access_service",
]
