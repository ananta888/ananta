"""Read one Hub-approved context projection; never hydrate a Hub repository."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from ananta_contracts.native_context_bundle import NativeContextBundleProjection, native_context_digest
from ananta_contracts.workflow_worker_gateway import WorkflowWorkerBinding
from worker.runtime.native_graph.contracts import NativeNodeCommand


class PiContextDecisionPort(Protocol):
    def command(self, command: str, *, binding: Mapping[str, Any], **values: Any) -> dict[str, Any]: ...


class PiTaskContextPort(Protocol):
    def read(
        self, *, task: Mapping[str, Any], command: NativeNodeCommand,
    ) -> NativeContextBundleProjection | None: ...


def pi_context_bundle_reference(task: Mapping[str, Any]) -> str | None:
    context = task.get("worker_execution_context")
    if not isinstance(context, Mapping):
        raise ValueError("pi_native_task_binding_mismatch")
    top, nested = task.get("context_bundle_id"), context.get("context_bundle_id")
    for value in (top, nested):
        if value is not None and (not isinstance(value, str) or not value or len(value) > 256):
            raise ValueError("pi_context_bundle_reference_invalid")
    if top and nested and top != nested:
        raise ValueError("pi_context_bundle_reference_mismatch")
    reference = top or nested
    if reference is None and context.get("context") is not None:
        raise ValueError("pi_context_bundle_reference_required")
    return reference


class HubPiTaskContextReader:
    def __init__(self, client: PiContextDecisionPort) -> None:
        self._client = client

    def read(
        self, *, task: Mapping[str, Any], command: NativeNodeCommand,
    ) -> NativeContextBundleProjection | None:
        bundle_id = pi_context_bundle_reference(task)
        if bundle_id is None:
            return None
        binding = WorkflowWorkerBinding(
            tenant_id=command.tenant_id, workflow_id=command.workflow_id, run_id=command.run_id,
            step_id=command.node.node_id, plan_hash=command.plan_hash, policy_version=command.policy_version,
            authorization_envelope=command.authorization.to_dict(),
        )
        projection = NativeContextBundleProjection.from_mapping(self._client.command(
            "native_context_read", binding=binding.to_dict(), hub_task_id=task.get("id"),
            command_id=command.command_id, attempt_id=command.attempt_id, fencing_token=command.fencing_token,
        ))
        if (
            projection.hub_task_id != task.get("id") or projection.bundle_id != bundle_id
            or projection.command_digest != native_context_digest(command.to_dict())
        ):
            raise ValueError("pi_context_projection_binding_mismatch")
        return projection
