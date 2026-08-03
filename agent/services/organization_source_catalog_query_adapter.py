"""Narrow read adapter over the productive Source Control query runtime."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from agent.services.source_control_projection_service import SourceControlPrincipal


class OrganizationSourceCatalogQueryError(RuntimeError):
    def __init__(self, reason_code: str, *, public_status: int = 409) -> None:
        self.reason_code = str(reason_code)
        self.public_status = int(public_status)
        super().__init__(self.reason_code)


@dataclass(frozen=True, slots=True)
class OrganizationSourceCatalogQueryPrincipal:
    subject_id: str
    tenant_id: str
    project_id: str
    roles: frozenset[str]
    project_role: str


@dataclass(frozen=True, slots=True)
class OrganizationSourceCatalogQueryBatch:
    knowledge_index_id: str
    matches: tuple[Mapping[str, Any], ...]


class OrganizationSourceCatalogQueryPort(Protocol):
    def query(
        self,
        *,
        principal: OrganizationSourceCatalogQueryPrincipal,
        connection_id: str,
        query: str,
        limit: int,
    ) -> OrganizationSourceCatalogQueryBatch: ...


class ProductionOrganizationSourceCatalogQueryAdapter:
    """Call the existing authorized, bounded active-index query seam."""

    def __init__(self, runtime: object | None) -> None:
        self._runtime = runtime

    def query(
        self,
        *,
        principal: OrganizationSourceCatalogQueryPrincipal,
        connection_id: str,
        query: str,
        limit: int,
    ) -> OrganizationSourceCatalogQueryBatch:
        execute = getattr(self._runtime, "query", None)
        if not callable(execute):
            raise OrganizationSourceCatalogQueryError(
                "organization_source_catalog_query_unavailable",
                public_status=503,
            )
        roles = set(principal.roles)
        if principal.project_role == "owner":
            roles.add("project_owner")
        if principal.project_role == "tenant_admin":
            roles.add("admin")
        try:
            raw = execute(
                principal=SourceControlPrincipal(
                    subject_id=principal.subject_id,
                    tenant_id=principal.tenant_id,
                    project_id=principal.project_id,
                    roles=frozenset(roles),
                ),
                connection_id=connection_id,
                payload={"query": query, "limit": int(limit)},
            )
        except Exception as exc:
            reason = str(getattr(exc, "reason_code", "") or "").strip()
            status = int(getattr(exc, "status_code", 409) or 409)
            raise OrganizationSourceCatalogQueryError(
                reason or "organization_source_catalog_query_failed",
                public_status=status,
            ) from exc
        if not isinstance(raw, Mapping):
            raise OrganizationSourceCatalogQueryError(
                "organization_source_catalog_query_result_invalid",
                public_status=502,
            )
        matches = raw.get("matches")
        artifact_status = raw.get("artifact_status")
        if (
            not isinstance(matches, list)
            or len(matches) > int(limit)
            or any(not isinstance(item, Mapping) for item in matches)
            or not isinstance(artifact_status, Mapping)
            or str(artifact_status.get("state") or "") != "available"
        ):
            raise OrganizationSourceCatalogQueryError(
                "organization_source_catalog_query_result_invalid",
                public_status=502,
            )
        knowledge_index_id = str(
            artifact_status.get("knowledge_index_id") or ""
        ).strip()
        if not knowledge_index_id:
            raise OrganizationSourceCatalogQueryError(
                "organization_source_catalog_query_lineage_missing",
                public_status=502,
            )
        return OrganizationSourceCatalogQueryBatch(
            knowledge_index_id=knowledge_index_id,
            matches=tuple(dict(item) for item in matches),
        )


__all__ = [
    "OrganizationSourceCatalogQueryBatch",
    "OrganizationSourceCatalogQueryError",
    "OrganizationSourceCatalogQueryPort",
    "OrganizationSourceCatalogQueryPrincipal",
    "ProductionOrganizationSourceCatalogQueryAdapter",
]
