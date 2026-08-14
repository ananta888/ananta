"""Production adapter from Native node commands to Ananta's Hub task queue."""

from __future__ import annotations

import hashlib
from typing import Any, Protocol

from agent.services.workflow_runtime.native_graph_contracts import (
    HubTaskReceipt,
    NativeNodeCommand,
    NativeNodeResult,
)


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
    ) -> None:
        self._queue = task_queue
        self._repository = task_repository
        self._runtime = task_runtime

    def submit(self, command: NativeNodeCommand) -> HubTaskReceipt:
        command.assert_valid()
        hub_task_id = _task_id(command.command_id)
        existing = self._repository.get_by_id(hub_task_id)
        if existing is not None:
            context = dict(getattr(existing, "worker_execution_context", None) or {})
            stored_id = str((context.get("native_node_command") or {}).get("command_id") or "")
            return HubTaskReceipt(
                hub_task_id=hub_task_id,
                command_id=command.command_id,
                accepted=stored_id == command.command_id,
                reason_code="" if stored_id == command.command_id else "native_hub_task_id_conflict",
            )
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
            tags=["workflow-runtime", "ananta-native"],
            event_type="workflow_node_task_created",
            event_details={
                "run_id": command.run_id,
                "workflow_id": command.workflow_id,
                "node_id": command.node.node_id,
                "command_id": command.command_id,
            },
            extra_fields={
                "task_kind": command.node.task_kind,
                "required_capabilities": list(command.node.required_capabilities),
                "derivation_reason": "native_graph_hub_delegation",
                "worker_execution_context": {
                    "schema": "ananta.native_graph_worker_context.v1",
                    "runtime_path": "native_graph_node",
                    "native_node_command": command.to_dict(),
                },
            },
        )
        return HubTaskReceipt(hub_task_id, command.command_id, True)

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
                result = NativeNodeResult.from_mapping(raw_result)
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
    from agent.services.task_queue_service import get_task_queue_service
    from agent.services.task_runtime_service import TaskRuntimeService

    return AnantaHubTaskQueueAdapter(
        task_queue=get_task_queue_service(),
        task_repository=task_repo,
        task_runtime=TaskRuntimeService(),
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
