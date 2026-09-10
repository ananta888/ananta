"""Production adapter from Native node commands to Ananta's Hub task queue."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any, Protocol

from agent.services.native_context_preparation_service import NativeContextPreparationPort
from agent.services.pi_native_result_validation import validate_pi_native_result
from agent.services.workflow_runtime.native_graph_contracts import (
    HubTaskReceipt,
    NativeNodeCommand,
    NativeNodeResult,
)
from ananta_contracts.native_context_bundle import native_context_digest


class TaskRepositoryPort(Protocol):
    def get_by_id(self, task_id: str) -> Any | None: ...


class TaskQueueMutationPort(Protocol):
    def ingest_task(self, **values: Any) -> None: ...


class TaskRuntimeMutationPort(Protocol):
    def update_local_task_status(self, task_id: str, status: str, **values: Any) -> None: ...


class AnantaHubTaskQueueAdapter:
    """Creates/polls real Hub tasks; it never executes a node in-process."""

    def __init__(
        self,
        *,
        task_queue: TaskQueueMutationPort,
        task_repository: TaskRepositoryPort,
        task_runtime: TaskRuntimeMutationPort,
        context_preparer: NativeContextPreparationPort | None = None,
    ) -> None:
        self._queue = task_queue
        self._repository = task_repository
        self._runtime = task_runtime
        self._context_preparer = context_preparer

    def submit(self, command: NativeNodeCommand) -> HubTaskReceipt:
        command.assert_valid()
        hub_task_id = _task_id(command.command_id)
        existing = self._repository.get_by_id(hub_task_id)
        if existing is not None:
            context = (
                existing.get("worker_execution_context") if isinstance(existing, Mapping)
                else getattr(existing, "worker_execution_context", None)
            )
            stored = context.get("native_node_command") if isinstance(context, Mapping) else None
            identical = isinstance(stored, Mapping) and native_context_digest(dict(stored)) == native_context_digest(
                command.to_dict()
            )
            return HubTaskReceipt(
                hub_task_id=hub_task_id,
                command_id=command.command_id,
                accepted=identical,
                reason_code="" if identical else "native_hub_task_id_conflict",
            )
        extra_fields = self._submission_fields(command, hub_task_id)
        team_id = extra_fields.pop("team_id", None)
        self._queue.ingest_task(
            task_id=hub_task_id,
            status="created",
            title=f"Workflow {command.workflow_id}: {command.node.node_id}",
            description=(
                "Execute one Hub-delegated Native workflow node. "
                f"run={command.run_id} node={command.node.node_id}"
            ),
            priority=str(command.node.metadata.get("priority") or "medium"),
            created_by="system:native-graph-orchestrator",
            source="workflow_runtime",
            team_id=team_id,
            tags=["workflow-runtime", "ananta-native"],
            event_type="workflow_node_task_created",
            event_details={
                "run_id": command.run_id,
                "workflow_id": command.workflow_id,
                "node_id": command.node.node_id,
                "command_id": command.command_id,
            },
            extra_fields=extra_fields,
        )
        return HubTaskReceipt(hub_task_id, command.command_id, True)

    def _submission_fields(self, command: NativeNodeCommand, hub_task_id: str) -> dict[str, Any]:
        fields = {
            "task_kind": command.node.task_kind,
            "required_capabilities": list(command.node.required_capabilities),
            "derivation_reason": "native_graph_hub_delegation",
            "worker_execution_context": {
                "schema": "ananta.native_graph_worker_context.v1",
                "runtime_path": "native_graph_node", "native_node_command": command.to_dict(),
            },
        }
        selectors = {"context_bundle_mode", "context_policy_id", "context_destination_id"}
        if selectors.isdisjoint(command.node.metadata):
            return fields
        if command.node.metadata.get("context_bundle_mode") != "control_task":
            raise ValueError("native_context_preparation_mode_invalid")
        if self._context_preparer is None:
            raise ValueError("native_context_preparer_unavailable")
        prepared = self._context_preparer.prepare(command=command, hub_task_id=hub_task_id)
        fields.update(prepared.scope)
        fields.update(parent_task_id=prepared.parent_task_id, context_bundle_id=prepared.bundle_id)
        return fields

    def poll(
        self, *, tenant_id: str, run_id: str, hub_task_ids: tuple[str, ...]
    ) -> tuple[NativeNodeResult, ...]:
        results: list[NativeNodeResult] = []
        for task_id in sorted(set(hub_task_ids)):
            task = self._repository.get_by_id(task_id)
            if task is None:
                continue
            context = dict(getattr(task, "worker_execution_context", None) or {})
            raw_command = dict(context.get("native_node_command") or {})
            if not raw_command:
                continue
            command = NativeNodeCommand.from_mapping(raw_command)
            if command.tenant_id != tenant_id or command.run_id != run_id:
                raise ValueError("native_hub_task_poll_binding_mismatch")
            status = str(getattr(task, "status", "") or "").strip().lower()
            if status not in {"completed", "failed", "cancelled"}:
                continue
            verification = dict(getattr(task, "verification_status", None) or {})
            raw_result = verification.get("native_node_result")
            if isinstance(raw_result, dict):
                result = (
                    validate_pi_native_result(raw_result, command=command, hub_task_id=task_id, task_status=status)
                    if command.node.task_kind == "pi_coding_agent" else NativeNodeResult.from_mapping(raw_result)
                )
                if result.hub_task_id != task_id:
                    raise ValueError("native_hub_task_result_id_mismatch")
                results.append(result)
                continue
            results.append(_missing_result_failure(command, task_id, status))
        return tuple(results)

    def cancel(
        self,
        *,
        tenant_id: str,
        run_id: str,
        hub_task_ids: tuple[str, ...],
        reason: str,
    ) -> None:
        for task_id in sorted(set(hub_task_ids)):
            task = self._repository.get_by_id(task_id)
            if task is None:
                continue
            context = dict(getattr(task, "worker_execution_context", None) or {})
            raw_command = dict(context.get("native_node_command") or {})
            if not raw_command:
                continue
            command = NativeNodeCommand.from_mapping(raw_command)
            if command.tenant_id != tenant_id or command.run_id != run_id:
                raise ValueError("native_hub_task_cancel_binding_mismatch")
            status = str(getattr(task, "status", "") or "").strip().lower()
            if status in {"completed", "failed", "cancelled"}:
                continue
            self._runtime.update_local_task_status(
                task_id,
                "cancelled",
                event_type="workflow_node_task_cancelled",
                event_actor="system:native-graph-orchestrator",
                event_details={"run_id": run_id, "reason": str(reason)[:240]},
            )


def build_native_graph_task_queue_adapter() -> AnantaHubTaskQueueAdapter:
    from agent.repository import task_repo
    from agent.services.native_context_preparation_composition import HubNativeContextPreparer
    from agent.services.task_queue_service import get_task_queue_service
    from agent.services.task_runtime_service import TaskRuntimeService

    return AnantaHubTaskQueueAdapter(
        task_queue=get_task_queue_service(),
        task_repository=task_repo,
        task_runtime=TaskRuntimeService(),
        context_preparer=HubNativeContextPreparer(),
    )


def _task_id(command_id: str) -> str:
    digest = hashlib.sha256(command_id.encode("utf-8")).hexdigest()[:24]
    return f"wfn-{digest}"


def _missing_result_failure(
    command: NativeNodeCommand, hub_task_id: str, task_status: str
) -> NativeNodeResult:
    return NativeNodeResult(
        result_id=f"nres-missing-{hub_task_id}",
        command_id=command.command_id,
        hub_task_id=hub_task_id,
        tenant_id=command.tenant_id,
        workflow_id=command.workflow_id,
        run_id=command.run_id,
        node_id=command.node.node_id,
        attempt_id=command.attempt_id,
        fencing_token=command.fencing_token,
        status="cancelled" if task_status == "cancelled" else "failed",
        reason_code="native_node_result_contract_missing",
    )
