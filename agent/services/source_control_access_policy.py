"""Common Hub authorization policy for governed source-control surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from agent.services.source_control_projection_service import (
    SourceControlPrincipal,
)


class SourceControlAccessPolicyError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class SourceControlAction(str, Enum):
    list = "list"
    detail = "detail"
    refresh = "refresh"
    scan = "scan"
    index = "index"
    graph = "graph"
    query = "query"
    policy = "policy"
    artifact = "artifact"
    download = "download"
    delete = "delete"
    LIST = list
    DETAIL = detail
    REFRESH = refresh
    SCAN = scan
    INDEX = index
    GRAPH = graph
    QUERY = query
    POLICY = policy
    ARTIFACT = artifact
    DOWNLOAD = download
    DELETE = delete


_MUTATING_ACTIONS = frozenset(
    {
        SourceControlAction.refresh,
        SourceControlAction.scan,
        SourceControlAction.index,
        SourceControlAction.delete,
    }
)


@dataclass(frozen=True)
class HubSourcePrincipal:
    subject_id: str
    tenant_id: str | None
    project_id: str | None
    roles: frozenset[str]

    def __post_init__(self) -> None:
        if not str(self.subject_id or "").strip():
            raise SourceControlAccessPolicyError(
                "source_control_principal_subject_required"
            )

    @property
    def is_admin(self) -> bool:
        return "admin" in self.roles

    @property
    def is_project_owner(self) -> bool:
        return "project_owner" in self.roles

    @property
    def is_project_maintainer(self) -> bool:
        return "project_maintainer" in self.roles

    @property
    def can_mutate_project(self) -> bool:
        return self.is_project_owner or self.is_project_maintainer

    def projection_principal(self) -> SourceControlPrincipal:
        if not self.tenant_id or not self.project_id:
            raise SourceControlAccessPolicyError(
                "source_control_principal_scope_required"
            )
        return SourceControlPrincipal(
            subject_id=self.subject_id,
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            roles=self.roles,
        )


@dataclass(frozen=True)
class SourceObjectBinding:
    object_id: str
    tenant_id: str | None
    project_id: str | None
    owner_id: str | None = None
    visible_subject_ids: frozenset[str] = frozenset()
    exists: bool = True
    binding_source: str = "direct"

    @property
    def is_scoped(self) -> bool:
        return bool(self.tenant_id and self.project_id)


@dataclass(frozen=True)
class SourceControlAccessDecision:
    allowed: bool
    status_code: int
    reason_code: str
    legacy_admin_access: bool = False


class SourceControlAccessPolicy:
    """Authorize one Hub action without revealing foreign object existence.

    Existing objects without a canonical tenant/project binding are legacy
    objects. They fail closed for every non-admin principal. Admin access is a
    documented compatibility path and must be audited by the route adapter.
    """

    def authorize(
        self,
        *,
        principal: HubSourcePrincipal,
        action: SourceControlAction,
        binding: SourceObjectBinding | None,
    ) -> SourceControlAccessDecision:
        if binding is None:
            if action is SourceControlAction.policy and not principal.is_admin:
                return self._forbidden(
                    "source_control_policy_admin_required"
                )
            if principal.is_admin:
                return self._allowed()
            if not principal.tenant_id or not principal.project_id:
                return self._forbidden(
                    "source_control_principal_scope_required"
                )
            if action in _MUTATING_ACTIONS and not principal.can_mutate_project:
                return self._forbidden(
                    "source_control_mutation_role_required"
                )
            return self._allowed()

        if not binding.exists:
            return self._hidden()

        if not binding.is_scoped:
            if principal.is_admin:
                return SourceControlAccessDecision(
                    allowed=True,
                    status_code=200,
                    reason_code="source_control_admin_legacy_access",
                    legacy_admin_access=True,
                )
            if action is SourceControlAction.policy:
                return self._forbidden(
                    "source_control_policy_admin_required"
                )
            return self._hidden()

        if principal.is_admin:
            return self._allowed()
        if (
            principal.tenant_id != binding.tenant_id
            or principal.project_id != binding.project_id
        ):
            return self._hidden()

        if (
            binding.owner_id
            and principal.subject_id != binding.owner_id
            and principal.subject_id not in binding.visible_subject_ids
            and not principal.is_project_owner
        ):
            return self._hidden()

        if action is SourceControlAction.policy:
            return self._forbidden(
                "source_control_policy_admin_required"
            )
        if action in _MUTATING_ACTIONS and not principal.can_mutate_project:
            return self._forbidden(
                "source_control_mutation_role_required"
            )
        return self._allowed()

    def can_view(
        self,
        *,
        principal: HubSourcePrincipal,
        binding: SourceObjectBinding,
    ) -> bool:
        return self.authorize(
            principal=principal,
            action=SourceControlAction.detail,
            binding=binding,
        ).allowed

    @staticmethod
    def _allowed() -> SourceControlAccessDecision:
        return SourceControlAccessDecision(
            allowed=True,
            status_code=200,
            reason_code="source_control_access_allowed",
        )

    @staticmethod
    def _hidden() -> SourceControlAccessDecision:
        return SourceControlAccessDecision(
            allowed=False,
            status_code=404,
            reason_code="resource_not_found",
        )

    @staticmethod
    def _forbidden(reason_code: str) -> SourceControlAccessDecision:
        return SourceControlAccessDecision(
            allowed=False,
            status_code=403,
            reason_code=reason_code,
        )


__all__ = [
    "HubSourcePrincipal",
    "SourceControlAccessDecision",
    "SourceControlAccessPolicy",
    "SourceControlAccessPolicyError",
    "SourceControlAction",
    "SourceObjectBinding",
]
