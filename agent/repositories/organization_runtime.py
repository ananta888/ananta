"""SQL adapters for Hub-owned Organization runtime ports.

Every adapter is bound to one immutable tenant/project/Organization scope.
This keeps callers from accidentally turning a local identifier into a
cross-tenant lookup and makes the scope part of every compare-and-swap.

Public entry point: each adapter lives in a responsibility-focused sibling
module and is re-exported here.
"""

from __future__ import annotations

from agent.repositories.organization_runtime_artifact_evidence import (
    SqlArtifactVersionReader,
    SqlAssignmentEvidenceVerifier,
)
from agent.repositories.organization_runtime_budget_ledger import SqlOrganizationBudgetLedger
from agent.repositories.organization_runtime_event_store import SqlOrganizationEventStore
from agent.repositories.organization_runtime_state_stores import (
    SqlHandoffStateStore,
    SqlOrganizationWorkflowLoopStore,
)
from agent.repositories.organization_runtime_support import SessionFactory  # noqa: F401 - legacy public alias

__all__ = [
    "SqlArtifactVersionReader",
    "SqlAssignmentEvidenceVerifier",
    "SqlHandoffStateStore",
    "SqlOrganizationBudgetLedger",
    "SqlOrganizationEventStore",
    "SqlOrganizationWorkflowLoopStore",
]
