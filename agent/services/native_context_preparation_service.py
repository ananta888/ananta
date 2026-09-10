"""Prepare a child-owned Pi bundle within the existing Hub submission flow."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from agent.services.native_context_bundle_service import NativeContextTaskRepositoryPort
from agent.services.native_context_snapshot import (
    NativeContextSnapshot,
    NativeContextSnapshotPort,
    bounded_context_identifier,
)
from agent.services.task_context_bundle_access_service import TaskContextBundleAccessPort
from agent.services.task_organization_scope import ORGANIZATION_SCOPE_ALL, inherited_organization_scope
from agent.services.workflow_runtime.native_graph_contracts import NativeNodeCommand


@dataclass(frozen=True, slots=True)
class NativePreparedTaskContext:
    bundle_id: str
    parent_task_id: str
    scope: Mapping[str, str]


class NativeContextPreparationPort(Protocol):
    def prepare(self, *, command: NativeNodeCommand, hub_task_id: str) -> NativePreparedTaskContext: ...


class NativeContextPreparationService:
    def __init__(
        self, *, tasks: NativeContextTaskRepositoryPort, bundles: TaskContextBundleAccessPort,
        snapshots: NativeContextSnapshotPort,
    ) -> None:
        self._tasks, self._bundles, self._snapshots = tasks, bundles, snapshots

    def prepare(self, *, command: NativeNodeCommand, hub_task_id: str) -> NativePreparedTaskContext:
        command.assert_valid()
        metadata = command.node.metadata
        if (
            command.node.task_kind != "pi_coding_agent" or metadata.get("context_bundle_mode") != "control_task"
            or command.provider_binding is None or not command.provider_profile_bindings
        ):
            raise ValueError("native_context_preparation_mode_invalid")
        access = {
            "policy_id": bounded_context_identifier(metadata.get("context_policy_id")),
            "destination_id": bounded_context_identifier(metadata.get("context_destination_id")),
            "provider_endpoint_identity": command.provider_binding.endpoint_identity,
        }
        task_id = bounded_context_identifier(command.control_task_id)
        task = _task_mapping(self._tasks.get_by_id(task_id))
        scope = inherited_organization_scope(task)
        if (
            task.get("id") != task_id or task.get("tenant_id") != command.tenant_id
            or scope.get("tenant_id") != command.tenant_id or not scope.get("project_id")
            or scope["project_id"] != task.get("project_id")
            or any(task.get(key) is not None and task[key] != scope.get(key) for key in ORGANIZATION_SCOPE_ALL)
            or task.get("status") not in {"created", "assigned", "queued", "running", "in_progress"}
        ):
            raise ValueError("native_context_control_task_binding_mismatch")
        bundle = self._bundles.resolve_task_reference(task=task, task_id=task_id)
        if bundle is None:
            raise ValueError("native_context_bundle_required")
        snapshot = NativeContextSnapshot.create(
            task_id=hub_task_id, command=command.to_dict(), source_bundle_id=_field(bundle, "id"),
            source_task_id=task_id, chunks=_field(bundle, "chunks"), access=access,
        )
        self._snapshots.ensure(snapshot)
        return NativePreparedTaskContext(snapshot.bundle_id, task_id, scope)


def _field(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def _task_mapping(stored: Any) -> Mapping[str, Any]:
    if isinstance(stored, Mapping):
        return stored
    if stored is not None and callable(getattr(stored, "model_dump", None)):
        value = stored.model_dump()
        if isinstance(value, Mapping):
            return value
    raise ValueError("native_context_control_task_required")
