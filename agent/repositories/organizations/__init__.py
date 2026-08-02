"""Session-bound Organization repositories.

The legacy team repositories remain exported from
``agent.repositories.organization``.  New aggregate writes use this package
and an explicit :class:`OrganizationUnitOfWork` transaction boundary.
"""

from .adapters import SqlOrganizationDefinitionCatalogAdapter, SqlOrganizationLimitProfileAdapter
from .definition_impacts import SqlOrganizationDefinitionImpactRepository
from .definitions import SqlOrganizationDefinitionRepository
from .instances import (
    SqlCrossTeamDependencyRepository,
    SqlOrganizationAdminGrantRepository,
    SqlOrganizationAssignmentRepository,
    SqlOrganizationInstanceRepository,
    SqlOrganizationLayoutRepository,
    SqlOrganizationMembershipRepository,
    SqlOrganizationRelationRepository,
    SqlOrganizationRoleSlotRepository,
    SqlOrganizationSnapshotRepository,
    SqlOrganizationTeamLinkRepository,
    SqlOrganizationTeamMaterializationRepository,
    SqlOrganizationTopologyPatchGrantRepository,
    SqlOrganizationUnitRepository,
)
from .operations import SqlOrganizationAuditOutboxRepository, SqlOrganizationOperationRepository
from .topology import SqlOrganizationTopologyReadRepository

__all__ = [
    "SqlCrossTeamDependencyRepository",
    "SqlOrganizationAdminGrantRepository",
    "SqlOrganizationAssignmentRepository",
    "SqlOrganizationAuditOutboxRepository",
    "SqlOrganizationDefinitionCatalogAdapter",
    "SqlOrganizationDefinitionImpactRepository",
    "SqlOrganizationDefinitionRepository",
    "SqlOrganizationInstanceRepository",
    "SqlOrganizationLayoutRepository",
    "SqlOrganizationLimitProfileAdapter",
    "SqlOrganizationMembershipRepository",
    "SqlOrganizationOperationRepository",
    "SqlOrganizationRelationRepository",
    "SqlOrganizationRoleSlotRepository",
    "SqlOrganizationSnapshotRepository",
    "SqlOrganizationTeamLinkRepository",
    "SqlOrganizationTeamMaterializationRepository",
    "SqlOrganizationTopologyReadRepository",
    "SqlOrganizationTopologyPatchGrantRepository",
    "SqlOrganizationUnitRepository",
]
