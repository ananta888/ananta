"""Authorization boundary for task-scoped source verification read models."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, NoReturn, Protocol

from agent.services.organization_membership_service import (
    OrganizationAccessPrincipal,
)
from agent.services.project_access_authority import (
    ProjectAccessError,
    ProjectCapability,
)
from agent.services.source_control_access_policy import HubSourcePrincipal


class TaskSourceProjectAccessPort(Protocol):
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


class TaskSourceOrganizationMembershipPort(Protocol):
    def can_view(
        self,
        *,
        principal: OrganizationAccessPrincipal,
        tenant_id: str,
        project_id: str,
        organization_id: str,
    ) -> bool: ...


class TaskSourceVerificationAccessError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.status_code = int(status_code)


class TaskSourceVerificationAccessService:
    """Require exact task scope, current project access and task ownership.

    Legacy tasks without a canonical tenant/project binding remain visible to
    administrators only.  Cross-scope and cross-owner denials intentionally
    use not-found semantics so the read endpoint cannot be used for task
    enumeration.
    """

    def require(
        self,
        *,
        task: Mapping[str, Any],
        principal: HubSourcePrincipal,
        project_access: TaskSourceProjectAccessPort | None,
        organization_membership: (
            TaskSourceOrganizationMembershipPort | None
        ) = None,
    ) -> None:
        tenant_id = str(task.get("tenant_id") or "").strip()
        project_id = str(task.get("project_id") or "").strip()
        if not tenant_id or not project_id:
            if principal.is_admin:
                return
            self._hide()

        # A scope-bearing token cannot be rebound to another tenant/project,
        # including when the token also carries an administrator role.
        if principal.tenant_id and principal.tenant_id != tenant_id:
            self._hide()
        if principal.project_id and principal.project_id != project_id:
            self._hide()
        if not principal.is_admin and not principal.tenant_id:
            self._hide()
        if project_access is None:
            raise TaskSourceVerificationAccessError(
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
                raise TaskSourceVerificationAccessError(
                    "organization_membership_service_unavailable",
                    status_code=503,
                )
            organization_principal = OrganizationAccessPrincipal(
                principal_id=principal.subject_id,
                tenant_id=tenant_id,
                project_id=project_id,
            )
            if not organization_membership.can_view(
                principal=organization_principal,
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization_id,
            ):
                self._hide()

        if principal.is_admin:
            return
        owner_id = self._task_owner(task)
        project_role = str(getattr(authorized_scope, "role", "") or "")
        if principal.subject_id != owner_id and project_role != "owner":
            self._hide()

    @staticmethod
    def _task_owner(task: Mapping[str, Any]) -> str:
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
        raise TaskSourceVerificationAccessError(
            "task_source_verification_not_found",
            status_code=404,
        )


_SERVICE = TaskSourceVerificationAccessService()


def get_task_source_verification_access_service() -> (
    TaskSourceVerificationAccessService
):
    return _SERVICE


__all__ = [
    "TaskSourceVerificationAccessError",
    "TaskSourceVerificationAccessService",
    "get_task_source_verification_access_service",
]
