"""Production Worker composition for the common Hub-gated tool pipeline."""

from __future__ import annotations

import logging
import threading
from typing import Any, Protocol

from worker.core.tool_calling_pipeline import (
    RecursiveToolRedactor,
    ToolCallDecision,
    ToolCallingPipeline,
    ToolCallRequest,
)
from worker.core.tool_descriptor_adapters import (
    AdaptedToolDescriptorRegistry,
    LangChainBuiltinToolCatalogSource,
)
from worker.core.tool_registry import ResourceLimits, ToolResult, WorkerToolEntry
from worker.runtime.workflow_hub_gateway import (
    HttpWorkflowHubDecisionClient,
    HubSideEffectLedgerAdapter,
    HubToolAuthorizationAdapter,
)

logger = logging.getLogger(__name__)


class HubBoundWorkerToolPolicy:
    """Local defense-in-depth over the Hub-signed tool allowlist."""

    def authorize(
        self, request: ToolCallRequest, descriptor: WorkerToolEntry
    ) -> ToolCallDecision:
        envelope_tools = {
            str(item)
            for item in request.authorization_envelope.get("allowed_tools", ())
        }
        if descriptor.id not in envelope_tools:
            return ToolCallDecision(False, "workflow_tool_not_in_signed_allowlist")
        if f"tool:{descriptor.id}" not in set(request.allowed_policy_scopes):
            return ToolCallDecision(False, "workflow_tool_policy_scope_missing")
        return ToolCallDecision(True, "workflow_tool_policy_allowed")


class BoundedWorkerToolBudget:
    """Per-process safety cap in addition to Hub plan/provider budgets."""

    def __init__(self, *, maximum_calls_per_run: int = 128) -> None:
        self._maximum = max(1, min(int(maximum_calls_per_run), 1024))
        self._usage: dict[tuple[str, str], int] = {}
        self._lock = threading.RLock()

    def reserve(
        self, request: ToolCallRequest, _descriptor: WorkerToolEntry
    ) -> ToolCallDecision:
        key = (request.tenant_id, request.run_id)
        with self._lock:
            used = self._usage.get(key, 0)
            if used >= self._maximum:
                return ToolCallDecision(False, "workflow_tool_call_budget_exceeded")
            self._usage[key] = used + 1
        return ToolCallDecision(
            True,
            "workflow_tool_call_budget_reserved",
            {"used": used + 1, "maximum": self._maximum},
        )


class HubConfirmedWorkerToolApproval:
    """Require the approval reference already verified by Hub authorization."""

    def authorize(
        self, request: ToolCallRequest, descriptor: WorkerToolEntry
    ) -> ToolCallDecision:
        if not descriptor.side_effects:
            return ToolCallDecision(True, "workflow_read_tool_approval_not_required")
        if not request.approval_ref or not request.hub_task_id:
            return ToolCallDecision(False, "workflow_tool_approval_required")
        return ToolCallDecision(
            True,
            "workflow_tool_approval_confirmed_by_hub",
            {"approval_id": request.approval_ref},
        )


class WorkerTaskLookupPort(Protocol):
    def get_by_id(self, task_id: str) -> Any | None: ...


class WorkerWorkspaceResolverPort(Protocol):
    def resolve_workspace_context(self, *, task: dict[str, Any]) -> Any: ...


class WorkerToolRuntimePort(Protocol):
    def dispatch(self, **values: Any) -> dict[str, Any]: ...


class WorkerRuntimeToolInvoker:
    """Invoke through injected Worker-only task, workspace and runtime ports."""

    def __init__(
        self,
        *,
        tasks: WorkerTaskLookupPort,
        workspaces: WorkerWorkspaceResolverPort,
        runtime: WorkerToolRuntimePort,
    ) -> None:
        self._tasks = tasks
        self._workspaces = workspaces
        self._runtime = runtime

    def invoke(
        self,
        request: ToolCallRequest,
        descriptor: WorkerToolEntry,
        *,
        limits: ResourceLimits,
    ) -> ToolResult:
        if not request.hub_task_id:
            return ToolResult.denied(
                request.tool_id,
                request.attempt_id,
                "workflow_tool_hub_task_binding_required",
            )
        try:
            task = self._tasks.get_by_id(request.hub_task_id)
            if task is None:
                return ToolResult.denied(
                    request.tool_id,
                    request.attempt_id,
                    "workflow_tool_task_not_found",
                )
            task_payload = task.model_dump()
            workspace = self._workspaces.resolve_workspace_context(
                task=task_payload
            )
            runtime_result = self._runtime.dispatch(
                tool_name=request.tool_id,
                arguments=request.arguments,
                task_id=request.hub_task_id,
                goal_id=str(task_payload.get("goal_id") or "") or None,
                workspace_ref=str(workspace.workspace_dir),
                mutation_mode=(
                    "controlled_workspace" if descriptor.side_effects else "read_only"
                ),
                policy_decision={
                    "decision": "allow",
                    "risk_class": descriptor.risk_class,
                    "reason": "hub_authorized_workflow_tool_pipeline",
                },
                tool_call_id=request.attempt_id,
                config={"max_result_chars": limits.max_output_chars},
            )
        except Exception as exc:  # noqa: BLE001 - execution boundary is fail-closed
            return ToolResult.denied(
                request.tool_id,
                request.attempt_id,
                f"workflow_tool_runtime_exception:{type(exc).__name__}",
            )
        success = str(runtime_result.get("status") or "").lower() == "ok"
        import json

        rendered = json.dumps(runtime_result, sort_keys=True, ensure_ascii=False, default=str)
        bounded = rendered[: limits.max_output_chars]
        result = ToolResult(
            tool_id=request.tool_id,
            execution_id=request.attempt_id,
            success=success,
            stdout=bounded if success else "",
            stderr="" if success else bounded,
            exit_code=0 if success else 1,
            artifacts=list(runtime_result.get("artifacts") or []),
            reason_code=(
                None
                if success
                else str(runtime_result.get("error") or "workflow_tool_execution_failed")[:256]
            ),
            truncated=len(rendered) > len(bounded),
            task_id=request.hub_task_id,
            command=request.tool_id,
        )
        return result


class UnavailableWorkerToolInvoker:
    """Fail closed when the Worker composition omitted an execution port."""

    def invoke(
        self,
        request: ToolCallRequest,
        descriptor: WorkerToolEntry,
        *,
        limits: ResourceLimits,
    ) -> ToolResult:
        del descriptor, limits
        return ToolResult.denied(
            request.tool_id,
            request.attempt_id,
            "workflow_tool_runtime_not_composed",
        )


class WorkerToolAudit:
    def record(self, event: dict[str, Any]) -> None:
        logger.info(
            "workflow_tool_event type=%s operation=%s tool=%s reason=%s",
            str(event.get("event_type") or ""),
            str(event.get("operation_id") or ""),
            str(event.get("tool_id") or ""),
            str(event.get("reason_code") or ""),
        )


def build_workflow_tool_pipeline(
    client: HttpWorkflowHubDecisionClient,
    *,
    registry: AdaptedToolDescriptorRegistry | None = None,
    invoker: WorkerRuntimeToolInvoker | UnavailableWorkerToolInvoker | None = None,
) -> ToolCallingPipeline:
    """Build the complete pipeline; Hub transport is mandatory."""

    if client is None:
        raise ValueError("workflow_hub_gateway_not_configured")
    resolved_registry = registry or AdaptedToolDescriptorRegistry(
        (LangChainBuiltinToolCatalogSource(),)
    )
    return ToolCallingPipeline(
        registry=resolved_registry,
        authorization=HubToolAuthorizationAdapter(client),
        policy=HubBoundWorkerToolPolicy(),
        budget=BoundedWorkerToolBudget(),
        approval=HubConfirmedWorkerToolApproval(),
        redaction=RecursiveToolRedactor(),
        ledger=HubSideEffectLedgerAdapter(client),
        invoker=invoker or UnavailableWorkerToolInvoker(),
        audit=WorkerToolAudit(),
    )


__all__ = [
    "BoundedWorkerToolBudget",
    "HubConfirmedWorkerToolApproval",
    "HubBoundWorkerToolPolicy",
    "WorkerRuntimeToolInvoker",
    "UnavailableWorkerToolInvoker",
    "WorkerToolAudit",
    "build_workflow_tool_pipeline",
]
