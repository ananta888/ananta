"""Narrow read ports used by the write-free organization compiler."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from agent.models.organization_models import (
    OrganizationBlueprintDefinition,
    OrganizationLimitProfile,
    TeamBlueprintDefinition,
)


@runtime_checkable
class OrganizationDefinitionCatalogPort(Protocol):
    def get_organization_blueprint(
        self,
        key: str,
        version: int,
    ) -> OrganizationBlueprintDefinition | None: ...

    def get_team_blueprint(
        self,
        key: str,
        version: int,
    ) -> TeamBlueprintDefinition | None: ...

    def has_role_template(self, key: str, version: int) -> bool: ...

    def has_workflow_definition(self, key: str, version: int) -> bool: ...

    def get_workflow_definition(self, key: str, version: int) -> dict[str, Any] | None: ...

    def has_handoff_definition(self, key: str, version: int) -> bool: ...

    def get_handoff_definition(self, key: str, version: int) -> dict[str, Any] | None: ...

    def has_policy(self, portable_ref: str) -> bool: ...


@runtime_checkable
class OrganizationLimitProfilePort(Protocol):
    def resolve_limit_profile(
        self,
        *,
        tenant_id: str,
        project_id: str,
        policy_ref: str,
    ) -> OrganizationLimitProfile: ...


@runtime_checkable
class OrganizationAdmissionPolicyPort(Protocol):
    def validate_exception(
        self,
        *,
        tenant_id: str,
        project_id: str,
        principal_id: str,
        exception_ref: str,
        definition_ref: str,
        definition_revision: str,
        policy_hash: str,
        composition_digest: str,
        composition: dict[str, int],
    ) -> tuple[bool, str | None]: ...


@runtime_checkable
class OrganizationRuntimeGuardPort(Protocol):
    """Hub-owned activity facts needed by topology resize planning."""

    def unit_activity(self, organization_id: str, unit_keys: list[str]) -> dict[str, dict[str, int]]: ...


@runtime_checkable
class OrganizationTopologyReadPort(Protocol):
    """One batch read; adapters must not issue per-node follow-up queries."""

    def load_topology_snapshot(
        self,
        *,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        include_runtime_overlay: bool,
        cursor: str | None,
        limit: int,
        max_depth: int,
        filters: dict[str, Any],
    ) -> dict[str, Any]: ...


__all__ = [
    "OrganizationAdmissionPolicyPort",
    "OrganizationDefinitionCatalogPort",
    "OrganizationLimitProfilePort",
    "OrganizationRuntimeGuardPort",
    "OrganizationTopologyReadPort",
]
