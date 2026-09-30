"""Normalized Hub-owned organization and definition persistence models.

Public entry point: the tables live in aggregate-focused sibling modules and
are re-exported here. Import order below preserves the historical metadata
registration order of the tables.
"""

from __future__ import annotations

# The module order is the table registration order in SQLModel.metadata.
# isort: off
from .organization_definitions import (
    RoleTemplateRevisionDB,
    TeamBlueprintRevisionDB,
    WorkflowDefinitionRevisionDB,
    OrganizationLimitProfileRevisionDB,
    OrganizationPolicyRevisionDB,
    OrganizationBlueprintRevisionDB,
    OrganizationHandoffDefinitionRevisionDB,
)
from .organization_topology import (
    OrganizationInstanceDB,
    OrganizationUnitDB,
    OrganizationTeamLinkDB,
    OrganizationRoleSlotDB,
    OrganizationRoleAssignmentDB,
    OrganizationRelationDB,
    OrganizationMembershipDB,
)
from .organization_access import (
    OrganizationAdminGrantDB,
    OrganizationTopologyPatchGrantDB,
    OrganizationAdmissionExceptionDB,
)
from .organization_projections import (
    OrganizationLayoutPreferenceDB,
    OrganizationTopologySnapshotDB,
)
from .organization_operations import (
    OrganizationOperationDB,
    OrganizationAuditOutboxDB,
    CrossTeamTaskDependencyDB,
)
# isort: on

__all__ = [
    "RoleTemplateRevisionDB",
    "TeamBlueprintRevisionDB",
    "WorkflowDefinitionRevisionDB",
    "OrganizationLimitProfileRevisionDB",
    "OrganizationPolicyRevisionDB",
    "OrganizationBlueprintRevisionDB",
    "OrganizationHandoffDefinitionRevisionDB",
    "OrganizationInstanceDB",
    "OrganizationUnitDB",
    "OrganizationTeamLinkDB",
    "OrganizationRoleSlotDB",
    "OrganizationRoleAssignmentDB",
    "OrganizationRelationDB",
    "OrganizationMembershipDB",
    "OrganizationAdminGrantDB",
    "OrganizationTopologyPatchGrantDB",
    "OrganizationAdmissionExceptionDB",
    "OrganizationLayoutPreferenceDB",
    "OrganizationTopologySnapshotDB",
    "OrganizationOperationDB",
    "OrganizationAuditOutboxDB",
    "CrossTeamTaskDependencyDB",
]
