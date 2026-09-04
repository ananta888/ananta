"""Hub-only composition root for the default-off native collaboration core."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from flask import Flask
from sqlmodel import Session

from agent.config import settings
from agent.database import engine
from agent.services.collaboration_agent_control_service import CollaborationAgentControlService
from agent.services.collaboration_binding_service import CollaborationBindingService
from agent.services.collaboration_bridge_ports import DisabledCollaborationBridge
from agent.services.collaboration_budget_service import CollaborationBudgetService
from agent.services.collaboration_command_service import CollaborationCommandService, PreauthorizedCommandPolicy
from agent.services.collaboration_delivery_service import (
    CollaborationDeliveryService,
    CollaborationProjectionService,
)
from agent.services.collaboration_domain_binding_authority import (
    HubCollaborationBindingAuthority,
    SqlModelCollaborationDomainCatalog,
)
from agent.services.collaboration_evidence_policy import CollaborationEvidencePolicy
from agent.services.collaboration_flow_projection_service import CollaborationFlowProjectionService
from agent.services.collaboration_legacy_migration_service import CollaborationLegacyMigrationService
from agent.services.collaboration_observability_service import CollaborationObservabilityService
from agent.services.collaboration_recovery_service import CollaborationRecoveryService
from agent.services.collaboration_search_service import CollaborationSearchService
from agent.services.collaboration_workspace_policy import CollaborationWorkspacePolicy
from agent.services.collaboration_workspace_service import CollaborationWorkspaceService
from agent.services.collaboration_workspace_store import CollaborationWorkspaceStore
from agent.services.hub_evidence_registry_service import get_hub_evidence_registry_service
from agent.services.share_session_service import get_share_session_service


@dataclass(frozen=True, slots=True)
class CollaborationWorkspaceWiringStatus:
    ready: bool
    reason_code: str | None


def initialize_collaboration_workspace(app: Flask) -> CollaborationWorkspaceWiringStatus:
    enabled = _bool(app.config.get("ANANTA_COLLABORATION_WORKSPACE_ENABLED", settings.collaboration_workspace_enabled))
    role = str(app.config.get("ROLE") or settings.role or "").strip().lower()
    if role != "hub":
        status = CollaborationWorkspaceWiringStatus(False, "collaboration_hub_role_required")
    elif not enabled:
        status = CollaborationWorkspaceWiringStatus(False, "collaboration_workspace_disabled")
    else:
        path = Path(
            str(app.config.get("ANANTA_COLLABORATION_WORKSPACE_STATE") or settings.collaboration_workspace_state)
        )
        store = CollaborationWorkspaceStore(path)
        policy = CollaborationWorkspacePolicy()
        budget = CollaborationBudgetService(
            store,
            limits={
                "tenant": 10_000,
                "workspace": 5_000,
                "room": 2_000,
                "principal": 1_000,
                "actor": 1_000,
                "task": 500,
                "provider": 500,
                "intent_chain": 64,
                "connection": 500,
            },
        )
        service = CollaborationWorkspaceService(
            store,
            policy=policy,
            evidence_policy=CollaborationEvidencePolicy(get_hub_evidence_registry_service()),
            budget=budget,
        )
        app.extensions["collaboration_workspace_service"] = service
        app.extensions["collaboration_delivery_service"] = CollaborationDeliveryService(store)
        projections = CollaborationProjectionService(store)
        search = CollaborationSearchService(store, policy=policy, budget=budget)
        app.extensions["collaboration_projection_service"] = projections
        app.extensions["collaboration_search_service"] = search
        app.extensions["collaboration_flow_projection_service"] = CollaborationFlowProjectionService(store)
        app.extensions["collaboration_observability_service"] = CollaborationObservabilityService(store)
        domain_catalog = SqlModelCollaborationDomainCatalog(lambda: Session(engine))
        app.extensions["collaboration_binding_service"] = CollaborationBindingService(
            store,
            policy=policy,
            authority=HubCollaborationBindingAuthority(store, domain_catalog),
        )
        app.extensions["collaboration_legacy_migration_service"] = CollaborationLegacyMigrationService(
            get_share_session_service(), service
        )
        app.extensions["collaboration_recovery_service"] = CollaborationRecoveryService(
            store, policy=policy, projections=projections, search=search
        )
        app.extensions["collaboration_agent_control_service"] = CollaborationAgentControlService(
            store,
            policy=policy,
            assignment_authority=_DenyAssignmentAuthority(),
            budget=budget,
        )
        app.extensions["collaboration_budget_service"] = budget
        allowed_tools = frozenset(
            value.strip()
            for value in str(app.config.get("ANANTA_COLLABORATION_AUTO_APPROVED_TOOLS") or "").split(",")
            if value.strip()
        )
        command_revision = int(app.config.get("ANANTA_COLLABORATION_COMMAND_POLICY_REVISION") or 1)
        app.extensions["collaboration_command_service"] = CollaborationCommandService(
            store,
            workspace_policy=policy,
            command_policy=PreauthorizedCommandPolicy(allowed_tools, command_revision),
            budget=budget,
        )
        app.extensions["collaboration_bridge"] = DisabledCollaborationBridge()
        status = CollaborationWorkspaceWiringStatus(True, None)
    app.extensions["collaboration_workspace_wiring_status"] = status
    return status


def _bool(value: object) -> bool:
    return value if isinstance(value, bool) else str(value or "").strip().lower() in {"1", "true", "yes", "on"}


class _DenyAssignmentAuthority:
    def decide(self, *, tenant_id: str, intent):
        del tenant_id, intent
        return {"authorized": False, "reason_code": "hub_assignment_authority_not_configured", "assignment": {}}


__all__ = ["CollaborationWorkspaceWiringStatus", "initialize_collaboration_workspace"]
