"""Session-scoped Organization persistence adapters composed for Hub routes.

Routes must not construct repositories directly. This small composition
facade builds the SQL-backed Organization adapters for one caller-owned
Session, so the route keeps owning the transaction boundary while the
service layer owns the repository wiring.
"""

from __future__ import annotations

from sqlmodel import Session

from agent.repositories.organizations.adapters import SqlOrganizationLimitProfileAdapter
from agent.repositories.organizations.definitions import SqlOrganizationDefinitionRepository
from agent.repositories.organizations.topology import SqlOrganizationTopologyReadRepository
from agent.services.organization_definition_catalog_service import (
    FileCatalogDefinitionRepositoryAdapter,
)


def catalog_definition_repository(session: Session, catalog) -> FileCatalogDefinitionRepositoryAdapter:
    """Definition repository overlaying the installed catalog on SQL definitions."""

    return FileCatalogDefinitionRepositoryAdapter(
        SqlOrganizationDefinitionRepository(session),
        catalog,
        session,
    )


def limit_profile_adapter(definitions) -> SqlOrganizationLimitProfileAdapter:
    """Limit-profile resolver over an already composed definition repository."""

    return SqlOrganizationLimitProfileAdapter(definitions)


def topology_read_repository(session: Session) -> SqlOrganizationTopologyReadRepository:
    """Read-only topology projection source bound to ``session``."""

    return SqlOrganizationTopologyReadRepository(session)


__all__ = [
    "catalog_definition_repository",
    "limit_profile_adapter",
    "topology_read_repository",
]
