"""Consume one already-delegated workflow adapter task on a Worker.

The consumer deliberately has no task-queue or orchestration dependency.  It
can only validate a Hub task, revalidate its lease/authority at the Hub, and
invoke one injected execution adapter.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from ananta_contracts.langgraph_hub_node import (
    LANGGRAPH_HUB_NODE_PAYLOAD_SCHEMA,
    langgraph_node_result,
)
from ananta_contracts.workflow_adapter_task import (
    WORKFLOW_ADAPTER_RUNTIME_PATH,
    WORKFLOW_ADAPTER_TASK_SCHEMA,
    WorkflowAdapterTask,
    WorkflowAdapterTaskContractError,
    WorkflowAdapterTaskResult,
)
from ananta_contracts.workflow_worker_gateway import (
    WorkflowWorkerBinding,
    WorkflowWorkerContractError,
)
from worker.adapters.workflow_adapter_base import DryRunResult, WorkflowArtifactResult
from worker.runtime.native_graph.contracts import NativeNodeCommand, NativeNodeResult

NATIVE_GRAPH_WORKER_CONTEXT_SCHEMA = "ananta.native_graph_worker_context.v1"
NATIVE_GRAPH_RUNTIME_PATH = "native_graph_node"


@dataclass(frozen=True)
class ExecutionAuthorizationDecision:
    allowed: bool
    reason_code: str


class HubExecutionAuthorizationPort(Protocol):
    def authorize(
        self,
        *,
        binding: WorkflowWorkerBinding,
        adapter_kind: str,
        attempt_id: str,
        fencing_token: int,
    ) -> ExecutionAuthorizationDecision: ...


class NativeTaskAdapterPort(Protocol):
    def execute_task(self, task: dict[str, Any]) -> NativeNodeResult: ...

    def verification_update(self, result: NativeNodeResult) -> dict[str, Any]: ...


class LangGraphExecutionAdapterPort(Protocol):
    def dry_run(
        self, *, task_id: str, task_type: str, payload: dict[str, Any]
    ) -> DryRunResult: ...

    def execute(
        self,
        *,
        task_id: str,
        task_type: str,
        payload: dict[str, Any],
        resume_token: str | None = None,
    ) -> WorkflowArtifactResult: ...


class WorkflowAdapterTaskConsumer:
    """Dispatch only recognized, pre-existing Hub task contracts."""

    def __init__(
        self,
        *,
        authorization: HubExecutionAuthorizationPort,
        native_adapter: NativeTaskAdapterPort | None = None,
        langgraph_adapter: LangGraphExecutionAdapterPort | None = None,
    ) -> None:
        self._authorization = authorization
        self._native_adapter = native_adapter
        self._langgraph_adapter = langgraph_adapter

    @staticmethod
    def supports(task: Mapping[str, Any]) -> bool:
        context = task.get("worker_execution_context")
        if not isinstance(context, Mapping):
            return False
        schema = str(context.get("schema") or "")
        runtime_path = str(context.get("runtime_path") or "")
        return (schema, runtime_path) in {
            (NATIVE_GRAPH_WORKER_CONTEXT_SCHEMA, NATIVE_GRAPH_RUNTIME_PATH),
            (WORKFLOW_ADAPTER_TASK_SCHEMA, WORKFLOW_ADAPTER_RUNTIME_PATH),
        }

    def consume(self, task: Mapping[str, Any]) -> WorkflowAdapterTaskResult:
        hub_task_id = str(task.get("id") or "").strip()
        if not hub_task_id or len(hub_task_id) > 256 or "\x00" in hub_task_id:
            return _failure("unknown", "native", "workflow_adapter_hub_task_id_invalid")
        context = task.get("worker_execution_context")
        if not isinstance(context, Mapping):
            return _failure(hub_task_id, "native", "workflow_adapter_context_required")
        schema = str(context.get("schema") or "")
        runtime_path = str(context.get("runtime_path") or "")
        if (schema, runtime_path) == (
            NATIVE_GRAPH_WORKER_CONTEXT_SCHEMA,
            NATIVE_GRAPH_RUNTIME_PATH,
        ):
            return self._consume_native(hub_task_id, task, context)
        if (schema, runtime_path) == (
            WORKFLOW_ADAPTER_TASK_SCHEMA,
            WORKFLOW_ADAPTER_RUNTIME_PATH,
        ):
            return self._consume_langgraph(hub_task_id, context)
        return WorkflowAdapterTaskResult(
            hub_task_id=hub_task_id,
            adapter_kind="native",
            status="unsupported",
            reason_code="workflow_adapter_worker_context_unsupported",
        )

    def _consume_native(
        self,
        hub_task_id: str,
        task: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> WorkflowAdapterTaskResult:
        try:
            command = NativeNodeCommand.from_mapping(
                dict(context.get("native_node_command") or {})
            )
        except (TypeError, ValueError) as exc:
            return _failure(hub_task_id, "native", _reason(exc, "native_node_command_invalid"))
        try:
            binding = WorkflowWorkerBinding.from_mapping(
                {
                    "tenant_id": command.tenant_id,
                    "workflow_id": command.workflow_id,
                    "run_id": command.run_id,
                    "step_id": command.node.node_id,
                    "plan_hash": command.plan_hash,
                    "policy_version": command.policy_version,
                    "authorization_envelope": command.authorization.to_dict(),
                }
            )
        except WorkflowWorkerContractError as exc:
            return _native_failure_result(command, hub_task_id, exc.reason_code)
        decision = self._authorize(
            binding=binding,
            adapter_kind="native",
            attempt_id=command.attempt_id,
            fencing_token=command.fencing_token,
        )
        if not decision.allowed:
            return _native_failure_result(command, hub_task_id, decision.reason_code)
        if self._native_adapter is None:
            return _native_failure_result(
                command,
                hub_task_id,
                "native_worker_adapter_not_configured",
            )
        try:
            result = self._native_adapter.execute_task(dict(task))
            result.assert_valid()
            verification = self._native_adapter.verification_update(result)
        except Exception as exc:  # noqa: BLE001 - adapter boundary must be fail-closed
            return _native_failure_result(
                command,
                hub_task_id,
                _reason(exc, "native_worker_adapter_failed"),
            )
        return _native_task_result(result, verification=verification)

    def _consume_langgraph(
        self,
        hub_task_id: str,
        context: Mapping[str, Any],
    ) -> WorkflowAdapterTaskResult:
        try:
            contract = WorkflowAdapterTask.from_mapping(context)
        except WorkflowAdapterTaskContractError as exc:
            return _failure(hub_task_id, "langgraph", exc.reason_code)
        decision = self._authorize(
            binding=contract.worker_binding(),
            adapter_kind=contract.adapter_kind,
            attempt_id=contract.attempt_id,
            fencing_token=contract.fencing_token,
        )
        if not decision.allowed:
            return _blocked(hub_task_id, contract.adapter_kind, decision.reason_code)
        if self._langgraph_adapter is None:
            return _unsupported(
                hub_task_id,
                contract.adapter_kind,
                "langgraph_worker_adapter_not_configured",
            )
        payload = contract.worker_payload()
        raw_plan = payload.get("execution_plan")
        if raw_plan is not None and (
            str(payload.get("schema") or "") != LANGGRAPH_HUB_NODE_PAYLOAD_SCHEMA
            or str(payload.get("execution_scope") or "") != "single_hub_node"
            or str(payload.get("delegated_node_id") or "") != contract.step_id
        ):
            return _blocked(
                hub_task_id,
                contract.adapter_kind,
                "langgraph_single_hub_node_contract_required",
            )
        try:
            if contract.command == "dry_run":
                dry_result = self._langgraph_adapter.dry_run(
                    task_id=hub_task_id,
                    task_type=contract.task_type,
                    payload=payload,
                )
                adapter_result = dry_result.as_dict()
                status = "blocked" if dry_result.blocked else "success"
                reason_code = dry_result.block_reason
                summary = (
                    f"LangGraph dry-run blocked: {dry_result.block_reason}"
                    if dry_result.blocked
                    else "LangGraph dry-run completed."
                )
                artifacts = _dry_run_node_artifacts(
                    payload,
                    dry_result=dry_result,
                    status="failed" if dry_result.blocked else "completed",
                )
                if artifacts:
                    adapter_result = {**adapter_result, "artifacts": list(artifacts)}
                sources: tuple[dict[str, Any], ...] = ()
            else:
                raw_resume_token = payload.pop("resume_token", None)
                live_result = self._langgraph_adapter.execute(
                    task_id=hub_task_id,
                    task_type=contract.task_type,
                    payload=payload,
                    resume_token=(
                        str(raw_resume_token) if raw_resume_token is not None else None
                    ),
                )
                adapter_result = live_result.as_dict()
                status = _adapter_status(live_result.status)
                reason_code = live_result.reason_code
                summary = live_result.summary
                artifacts = tuple(dict(item) for item in live_result.artifacts)
                sources = tuple(dict(item) for item in live_result.sources)
        except Exception as exc:  # noqa: BLE001 - adapter boundary must be fail-closed
            return _failure(
                hub_task_id,
                contract.adapter_kind,
                _reason(exc, "langgraph_worker_adapter_failed"),
            )
        final_decision = self._authorize(
            binding=contract.worker_binding(),
            adapter_kind=contract.adapter_kind,
            attempt_id=contract.attempt_id,
            fencing_token=contract.fencing_token,
        )
        if not final_decision.allowed:
            return _blocked(
                hub_task_id,
                contract.adapter_kind,
                final_decision.reason_code or "workflow_execution_fenced",
            )
        try:
            outcome = WorkflowAdapterTaskResult(
                hub_task_id=hub_task_id,
                adapter_kind=contract.adapter_kind,
                status=status,
                reason_code=reason_code,
                summary=summary,
                artifacts=artifacts,
                sources=sources,
                adapter_result=adapter_result,
            )
            outcome.validate()
            return outcome
        except WorkflowAdapterTaskContractError as exc:
            return _failure(hub_task_id, contract.adapter_kind, exc.reason_code)

    def _authorize(
        self,
        *,
        binding: WorkflowWorkerBinding,
        adapter_kind: str,
        attempt_id: str,
        fencing_token: int,
    ) -> ExecutionAuthorizationDecision:
        try:
            decision = self._authorization.authorize(
                binding=binding,
                adapter_kind=adapter_kind,
                attempt_id=attempt_id,
                fencing_token=fencing_token,
            )
        except Exception as exc:  # noqa: BLE001 - unavailable Hub means deny
            return ExecutionAuthorizationDecision(
                False,
                _reason(exc, "workflow_hub_authorization_unavailable"),
            )
        if not isinstance(decision, ExecutionAuthorizationDecision):
            return ExecutionAuthorizationDecision(False, "workflow_hub_decision_invalid")
        return decision


def _dry_run_node_artifacts(
    payload: Mapping[str, Any],
    *,
    dry_result: DryRunResult,
    status: str,
) -> tuple[dict[str, Any], ...]:
    node_id = str(payload.get("delegated_node_id") or "").strip()
    plan_hash = str(payload.get("plan_hash") or "").strip()
    if not node_id or not plan_hash:
        return ()
    return (
        langgraph_node_result(
            node_id=node_id,
            status=status,
            plan_hash=plan_hash,
            reason_code=dry_result.block_reason,
            value={"dry_run": dry_result.as_dict()},
        ),
    )


def _native_task_result(
    result: NativeNodeResult,
    *,
    verification: dict[str, Any],
) -> WorkflowAdapterTaskResult:
    adapter_result = result.to_dict()
    status = {"completed": "success", "cancelled": "cancelled"}.get(
        result.status, "failed"
    )
    artifacts = tuple(
        {
            "artifact_id": str(name),
            "reference": str(reference),
        }
        for name, reference in sorted(result.artifact_refs.items())
    )
    return WorkflowAdapterTaskResult(
        hub_task_id=result.hub_task_id,
        adapter_kind="native",
        status=status,
        reason_code=result.reason_code,
        summary=(
            "Native delegated node completed."
            if status == "success"
            else "Native delegated node did not complete."
        ),
        artifacts=artifacts,
        adapter_result={**adapter_result, "verification": verification},
    )


def _native_failure_result(
    command: NativeNodeCommand,
    hub_task_id: str,
    reason_code: str,
) -> WorkflowAdapterTaskResult:
    reason = _reason_code(reason_code, "native_worker_adapter_failed")
    digest = hashlib.sha256(
        f"{hub_task_id}:{command.command_id}:{command.attempt_id}:{reason}".encode()
    ).hexdigest()[:24]
    native_result = NativeNodeResult(
        result_id=f"nres-consumer-{digest}",
        command_id=command.command_id,
        hub_task_id=hub_task_id,
        tenant_id=command.tenant_id,
        workflow_id=command.workflow_id,
        run_id=command.run_id,
        node_id=command.node.node_id,
        attempt_id=command.attempt_id,
        fencing_token=command.fencing_token,
        status="failed",
        reason_code=reason,
    )
    return _native_task_result(
        native_result,
        verification={
            "schema": "ananta.native_graph_task_verification.v1",
            "native_node_result": native_result.to_dict(),
        },
    )


def _adapter_status(value: str) -> str:
    return {
        "success": "success",
        "blocked": "blocked",
        "failed": "failed",
        "cancelled": "cancelled",
    }.get(str(value), "failed")


def _blocked(hub_task_id: str, adapter_kind: str, reason_code: str) -> WorkflowAdapterTaskResult:
    return WorkflowAdapterTaskResult(
        hub_task_id=hub_task_id,
        adapter_kind=adapter_kind,
        status="blocked",
        reason_code=_reason_code(reason_code, "workflow_hub_authorization_denied"),
    )


def _unsupported(
    hub_task_id: str, adapter_kind: str, reason_code: str
) -> WorkflowAdapterTaskResult:
    return WorkflowAdapterTaskResult(
        hub_task_id=hub_task_id,
        adapter_kind=adapter_kind,
        status="unsupported",
        reason_code=_reason_code(reason_code, "workflow_adapter_unsupported"),
    )


def _failure(hub_task_id: str, adapter_kind: str, reason_code: str) -> WorkflowAdapterTaskResult:
    return WorkflowAdapterTaskResult(
        hub_task_id=hub_task_id,
        adapter_kind=adapter_kind,
        status="failed",
        reason_code=_reason_code(reason_code, "workflow_adapter_execution_failed"),
    )


def _reason(exc: Exception, fallback: str) -> str:
    return _reason_code(getattr(exc, "reason_code", str(exc)), fallback)


def _reason_code(value: object, fallback: str) -> str:
    candidate = str(value or "").strip()
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:,-]{0,159}", candidate):
        return candidate
    return fallback


__all__ = [
    "ExecutionAuthorizationDecision",
    "HubExecutionAuthorizationPort",
    "NATIVE_GRAPH_RUNTIME_PATH",
    "NATIVE_GRAPH_WORKER_CONTEXT_SCHEMA",
    "WorkflowAdapterTaskConsumer",
]
