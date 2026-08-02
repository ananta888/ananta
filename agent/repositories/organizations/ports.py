"""Small persistence ports for the Organization aggregate."""

from __future__ import annotations

from typing import Any, Protocol, TypeVar

T = TypeVar("T")


class AddPort(Protocol[T]):
    def add(self, row: T) -> T: ...


class OrganizationDefinitionRepositoryPort(Protocol):
    def get_organization_blueprint(self, tenant_id: str, project_id: str, key: str, version: int): ...
    def get_team_blueprint(self, tenant_id: str, project_id: str, key: str, version: int): ...
    def get_role_template(self, tenant_id: str, project_id: str, key: str, version: int): ...
    def get_workflow(self, tenant_id: str, project_id: str, key: str, version: int): ...
    def get_handoff(self, tenant_id: str, project_id: str, key: str, version: int): ...
    def get_limit_profile(self, tenant_id: str, project_id: str, key: str, revision: int): ...
    def add(self, row: Any) -> Any: ...


class OrganizationDefinitionImpactRepositoryPort(Protocol):
    def list_active_instance_ids(
        self,
        tenant_id: str,
        project_id: str,
        key: str,
        version: int,
        *,
        for_update: bool = False,
    ) -> list[str]: ...

    def list_snapshot_hashes(
        self,
        tenant_id: str,
        project_id: str,
        key: str,
        version: int,
    ) -> list[str]: ...

    def list_assignment_links(
        self,
        tenant_id: str,
        project_id: str,
        key: str,
        version: int,
    ) -> list[dict[str, str | None]]: ...


class OrganizationInstanceRepositoryPort(Protocol):
    def get_scoped(self, tenant_id: str, project_id: str, organization_id: str, *, for_update: bool = False): ...
    def get_by_idempotency_key(self, tenant_id: str, project_id: str, idempotency_key: str): ...
    def add(self, row: Any) -> Any: ...


class OrganizationUnitRepositoryPort(Protocol):
    def list_for_organization(self, tenant_id: str, project_id: str, organization_id: str) -> list[Any]: ...
    def add_many(self, rows: list[Any]) -> list[Any]: ...


class OrganizationTopologyRepositoryPort(Protocol):
    def load_topology_snapshot(self, **kwargs: Any) -> dict[str, Any]: ...


class OrganizationOperationRepositoryPort(Protocol):
    def get_by_idempotency_key(
        self,
        tenant_id: str,
        project_id: str,
        operation_kind: str,
        idempotency_key: str,
        *,
        for_update: bool = False,
    ): ...
    def add(self, row: Any) -> Any: ...


class OrganizationUnitOfWorkPort(Protocol):
    definitions: OrganizationDefinitionRepositoryPort
    definition_impacts: OrganizationDefinitionImpactRepositoryPort
    instances: OrganizationInstanceRepositoryPort
    units: OrganizationUnitRepositoryPort
    topology: OrganizationTopologyRepositoryPort
    operations: OrganizationOperationRepositoryPort

    def __enter__(self): ...
    def __exit__(self, exc_type, exc_value, traceback) -> None: ...
    def flush(self) -> None: ...


__all__ = [
    "AddPort",
    "OrganizationDefinitionRepositoryPort",
    "OrganizationDefinitionImpactRepositoryPort",
    "OrganizationInstanceRepositoryPort",
    "OrganizationOperationRepositoryPort",
    "OrganizationTopologyRepositoryPort",
    "OrganizationUnitOfWorkPort",
    "OrganizationUnitRepositoryPort",
]
