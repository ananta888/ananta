"""Production composition root for worker-to-Hub workflow decisions."""

from __future__ import annotations

import threading
from typing import Any

from agent.services.workflow_hub_task_gateway_runtime import (
    get_workflow_authorization_key_ring,
)
from agent.services.workflow_runtime import (
    AuthorizationVerifier,
    SQLAlchemyEventStore,
    SQLAlchemyExecutionOwnershipStore,
    SQLAlchemyProviderBudgetStore,
    SQLAlchemySideEffectLedger,
)
from agent.services.workflow_worker_gateway_service import (
    WorkflowToolApprovalDecision,
    WorkflowToolDescriptor,
    WorkflowWorkerGatewayService,
)

_SERVICE: WorkflowWorkerGatewayService | None = None
_LOCK = threading.RLock()


class _AnantaRegistryWorkflowToolDescriptorAdapter:
    """Map the existing Hub registry onto the segregated descriptor port."""

    _COMPATIBILITY_READ_TOOLS = frozenset({"search_code", "summarize_doc"})
    _SIDE_EFFECT_CLASS_BY_CATEGORY = {
        "read_only": "read",
        "controlled_execution": "non_idempotent_write",
        "controlled_write": "idempotent_write",
    }

    def __init__(self, service: Any) -> None:
        self._service = service

    def resolve(self, tool_id: str) -> WorkflowToolDescriptor | None:
        normalized = str(tool_id or "").strip()
        spec = self._service.get_tool(normalized)
        if spec is None:
            if normalized in self._COMPATIBILITY_READ_TOOLS:
                return WorkflowToolDescriptor(normalized, "read")
            return None
        side_effect_class = self._SIDE_EFFECT_CLASS_BY_CATEGORY.get(
            str(spec.category)
        )
        if side_effect_class is None:
            return None
        return WorkflowToolDescriptor(normalized, side_effect_class)


class _ApprovalRequestToolApprovalAdapter:
    """Project the persistent approval lifecycle onto the small gateway port."""

    def __init__(self, service: Any) -> None:
        self._service = service

    def authorize(
        self,
        *,
        approval_ref: str,
        tool_id: str,
        arguments: dict[str, Any],
        hub_task_id: str,
        goal_id: str | None,
    ) -> WorkflowToolApprovalDecision:
        explicit = self._service.get_request(approval_ref)
        grant = self._service.resolve_grant_for_call(
            tool_name=tool_id,
            arguments=arguments,
            task_id=hub_task_id,
            goal_id=goal_id,
        )
        if explicit is None or grant is None or explicit.id != grant.id:
            return WorkflowToolApprovalDecision(
                False,
                "workflow_tool_approval_binding_mismatch",
            )
        return WorkflowToolApprovalDecision(
            True,
            "workflow_tool_approval_granted",
            approval_id=str(grant.id),
        )

    def consume(self, approval_ref: str) -> bool:
        return self._service.consume_request(approval_ref) is not None


def get_workflow_worker_gateway_service() -> WorkflowWorkerGatewayService:
    global _SERVICE
    if _SERVICE is not None:
        return _SERVICE
    with _LOCK:
        if _SERVICE is None:
            from agent.database import engine
            from agent.services.ananta_tool_registry_service import (
                get_ananta_tool_registry_service,
            )
            from agent.services.approval_request_service import (
                get_approval_request_service,
            )
            from agent.services.workflow_authorization_grant_service import (
                SQLAlchemyWorkflowAuthorizationGrantService,
            )
            from agent.services.workflow_control_persistence import (
                SQLAlchemyWorkflowCommandReplayNonceStore,
            )
            from agent.services.workflow_runtime.telemetry_runtime import configure_workflow_telemetry

            _SERVICE = WorkflowWorkerGatewayService(
                authorization=AuthorizationVerifier(
                    get_workflow_authorization_key_ring(),
                    SQLAlchemyWorkflowCommandReplayNonceStore(engine),
                ),
                ownership=SQLAlchemyExecutionOwnershipStore(engine),
                ledger=SQLAlchemySideEffectLedger(engine),
                provider_budgets=SQLAlchemyProviderBudgetStore(engine),
                events=configure_workflow_telemetry(SQLAlchemyEventStore(engine)),
                authorization_revalidator=SQLAlchemyWorkflowAuthorizationGrantService(
                    engine
                ),
                tool_approvals=_ApprovalRequestToolApprovalAdapter(
                    get_approval_request_service()
                ),
                tool_descriptors=_AnantaRegistryWorkflowToolDescriptorAdapter(
                    get_ananta_tool_registry_service()
                ),
            )
    return _SERVICE


def reset_workflow_worker_gateway_service() -> None:
    global _SERVICE
    with _LOCK:
        _SERVICE = None


__all__ = [
    "get_workflow_worker_gateway_service",
    "reset_workflow_worker_gateway_service",
]
