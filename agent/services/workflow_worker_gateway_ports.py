"""Error type, collaborator and tool ports, and fail-closed default adapters of the workflow worker gateway."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping

    from agent.services.workflow_runtime import RuntimeAuthorizationEnvelope
    from ananta_contracts.workflow_worker_gateway import WorkflowWorkerBinding


class WorkflowWorkerGatewayError(RuntimeError):
    def __init__(self, reason_code: str, *, status_code: int = 409) -> None:
        self.reason_code = str(reason_code or "workflow_worker_gateway_failed")
        self.status_code = int(status_code)
        super().__init__(self.reason_code)


@dataclass(frozen=True)
class WorkflowToolApprovalDecision:
    """Hub decision for one exact, digest-bound Worker tool call."""

    allowed: bool
    reason_code: str
    approval_id: str = ""


class WorkflowToolApprovalPort(Protocol):
    """Small Hub-owned port; Workers never access approval persistence."""

    def authorize(
        self,
        *,
        approval_ref: str,
        tool_id: str,
        arguments: dict[str, Any],
        hub_task_id: str,
        goal_id: str | None,
    ) -> WorkflowToolApprovalDecision: ...

    def consume(self, approval_ref: str) -> bool: ...


@dataclass(frozen=True)
class WorkflowToolDescriptor:
    """Hub-authoritative classification for one registered tool operation."""

    tool_id: str
    side_effect_class: str


class WorkflowToolDescriptorPort(Protocol):
    """Resolve classification without exposing the concrete Hub registry."""

    def resolve(self, tool_id: str) -> WorkflowToolDescriptor | None: ...


class WorkflowWorkerAuthorityPort(Protocol):
    """Verify signed Hub authority and the active ownership fence of one command."""

    def verify(
        self,
        binding: WorkflowWorkerBinding,
        raw: Mapping[str, Any],
        *,
        tool: str = "",
        requested_budget: dict[str, int | float] | None = None,
        writing: bool = False,
    ) -> RuntimeAuthorizationEnvelope: ...

    def ownership_binding(
        self,
        binding: WorkflowWorkerBinding,
        raw: Mapping[str, Any],
    ) -> tuple[str, int]: ...


class WorkflowToolGuardPort(Protocol):
    """Bind a tool call to its Hub descriptor and its digest-bound approval."""

    def tool_binding(
        self,
        binding: WorkflowWorkerBinding,
        raw: Mapping[str, Any],
    ) -> tuple[str, str, str]: ...

    def approval_decision(
        self,
        raw: Mapping[str, Any],
        *,
        tool_id: str,
        side_effect_class: str,
    ) -> WorkflowToolApprovalDecision: ...

    def consume_approval(self, approval_id: str) -> bool: ...


class WorkflowWorkerEventRecorderPort(Protocol):
    """Append Hub-owned gateway observations to the canonical event store."""

    def append(
        self,
        binding: WorkflowWorkerBinding,
        *,
        event_type: str,
        dedupe_key: str,
        causation_id: str,
        payload: dict[str, Any],
    ) -> None: ...

    def append_side_effect(
        self,
        binding: WorkflowWorkerBinding,
        record: Any,
        *,
        causation_id: str,
    ) -> None: ...


class UnavailableWorkflowToolApprovalService:
    """Fail-closed default for compositions without approval persistence."""

    def authorize(
        self,
        *,
        approval_ref: str,
        tool_id: str,
        arguments: dict[str, Any],
        hub_task_id: str,
        goal_id: str | None,
    ) -> WorkflowToolApprovalDecision:
        del approval_ref, tool_id, arguments, hub_task_id, goal_id
        return WorkflowToolApprovalDecision(
            False,
            "workflow_tool_approval_unavailable",
        )

    def consume(self, approval_ref: str) -> bool:
        del approval_ref
        return False


class UnavailableWorkflowToolDescriptorService:
    """Fail closed when the Hub descriptor registry is not composed."""

    def resolve(self, tool_id: str) -> WorkflowToolDescriptor | None:
        del tool_id
        return None
