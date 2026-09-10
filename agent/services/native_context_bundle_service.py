"""Task-bound context delivery behind the authenticated workflow Hub gateway."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from agent.services.task_context_bundle_access_service import TaskContextBundleAccessPort
from agent.services.workflow_runtime.native_graph_contracts import NativeNodeCommand
from ananta_contracts.native_context_bundle import NativeApprovedContext, NativeContextBundleProjection
from ananta_contracts.workflow_worker_gateway import WorkflowWorkerBinding


class NativeContextTaskRepositoryPort(Protocol):
    def get_by_id(self, task_id: str) -> Any | None: ...


class NativeContextPolicyPort(Protocol):
    def project(
        self, *, task: Mapping[str, Any], bundle: Any, command: NativeNodeCommand, worker_id: str,
    ) -> NativeApprovedContext: ...


class NativeContextBundleReadPort(Protocol):
    def read(
        self, *, binding: WorkflowWorkerBinding, hub_task_id: str, command_id: str,
        attempt_id: str, fencing_token: int, worker_id: str,
    ) -> NativeContextBundleProjection: ...


class NativeContextBundleService:
    """Read only persisted Hub context, after gateway signature/lease checks.

Task/bundle ownership and destination policy remain independent boundaries.
This service cannot create a bundle, grant, task, identity or assignment.
"""

    def __init__(
        self, *, tasks: NativeContextTaskRepositoryPort, bundles: TaskContextBundleAccessPort,
        policy: NativeContextPolicyPort,
    ) -> None:
        self._tasks, self._bundles, self._policy = tasks, bundles, policy

    def read(
        self, *, binding: WorkflowWorkerBinding, hub_task_id: str, command_id: str,
        attempt_id: str, fencing_token: int, worker_id: str,
    ) -> NativeContextBundleProjection:
        for value in (hub_task_id, command_id, worker_id):
            if not isinstance(value, str) or not value or len(value) > 256 or any(ord(c) < 33 for c in value):
                raise ValueError("native_context_request_invalid")
        task = self._task(hub_task_id)
        command = self._bound_command(
            task, binding=binding, hub_task_id=hub_task_id, command_id=command_id,
            attempt_id=attempt_id, fencing_token=fencing_token,
        )
        bundle = self._bundles.resolve_task_reference(task=task, task_id=hub_task_id)
        if bundle is None:
            raise ValueError("native_context_bundle_required")
        bundle_id = bundle.get("id") if isinstance(bundle, Mapping) else getattr(bundle, "id", None)
        approved = self._policy.project(task=task, bundle=bundle, command=command, worker_id=worker_id)
        if not isinstance(approved, NativeApprovedContext):
            raise ValueError("native_context_policy_result_invalid")
        return NativeContextBundleProjection.create(
            hub_task_id=hub_task_id, bundle_id=bundle_id, command=command.to_dict(), approved=approved,
        )

    def _task(self, hub_task_id: str) -> dict[str, Any]:
        stored = self._tasks.get_by_id(hub_task_id)
        if isinstance(stored, Mapping):
            return dict(stored)
        if stored is not None and callable(getattr(stored, "model_dump", None)):
            return stored.model_dump()
        raise ValueError("native_context_task_not_found")

    @staticmethod
    def _bound_command(
        task: Mapping[str, Any], *, binding: WorkflowWorkerBinding, hub_task_id: str,
        command_id: str, attempt_id: str, fencing_token: int,
    ) -> NativeNodeCommand:
        worker_context = task.get("worker_execution_context")
        if (
            task.get("id") != hub_task_id or task.get("tenant_id") != binding.tenant_id
            or not isinstance(task.get("project_id"), str) or not task["project_id"].strip()
            or task.get("task_kind") != "pi_coding_agent"
            or task.get("derivation_reason") != "native_graph_hub_delegation"
            or task.get("status") not in {"created", "assigned", "queued", "running", "in_progress"}
            or not isinstance(worker_context, Mapping)
            or worker_context.get("schema") != "ananta.native_graph_worker_context.v1"
            or worker_context.get("runtime_path") != "native_graph_node"
        ):
            raise ValueError("native_context_task_binding_mismatch")
        raw = worker_context.get("native_node_command")
        if not isinstance(raw, dict):
            raise ValueError("native_context_command_required")
        command = NativeNodeCommand.from_mapping(raw)
        if (
            command.command_id != command_id or command.tenant_id != binding.tenant_id
            or command.workflow_id != binding.workflow_id or command.run_id != binding.run_id
            or command.node.node_id != binding.step_id or command.plan_hash != binding.plan_hash
            or command.policy_version != binding.policy_version or command.attempt_id != attempt_id
            or type(fencing_token) is not int or command.fencing_token != fencing_token
            or command.authorization.to_dict() != binding.authorization_envelope
            or command.node.task_kind != "pi_coding_agent"
            or command.provider_binding is None or not command.provider_profile_bindings
        ):
            raise ValueError("native_context_command_binding_mismatch")
        return command
