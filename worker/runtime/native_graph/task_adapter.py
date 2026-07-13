"""Worker adapter for an already delegated Native graph Hub task."""

from __future__ import annotations

import contextlib
from typing import Any, Mapping, Protocol

from worker.runtime.native_graph.contracts import NativeNodeCommand, NativeNodeResult
from worker.runtime.native_graph.node_runtime import NativeDelegatedNodeRuntime


class NativeGraphWorkerTaskAdapter:
    def __init__(
        self,
        runtime: NativeDelegatedNodeRuntime,
        *,
        execution_scope: "NativeExecutionScopePort | None" = None,
    ) -> None:
        self._runtime = runtime
        self._execution_scope = execution_scope

    def execute_task(self, task: dict[str, Any]) -> NativeNodeResult:
        task_id = str(task.get("id") or "").strip()
        context = dict(task.get("worker_execution_context") or {})
        if context.get("schema") != "ananta.native_graph_worker_context.v1":
            raise ValueError("native_graph_worker_context_schema_unsupported")
        if context.get("runtime_path") != "native_graph_node":
            raise ValueError("native_graph_worker_runtime_path_mismatch")
        command = NativeNodeCommand.from_mapping(dict(context.get("native_node_command") or {}))
        scope = (
            self._execution_scope.bind(command, task=task)
            if self._execution_scope is not None
            else contextlib.nullcontext()
        )
        with scope:
            return self._runtime.execute(command, hub_task_id=task_id)

    @staticmethod
    def verification_update(result: NativeNodeResult) -> dict[str, Any]:
        """Canonical payload the worker callback persists on the Hub task."""

        return {
            "schema": "ananta.native_graph_task_verification.v1",
            "native_node_result": result.to_dict(),
        }


class NativeExecutionScopePort(Protocol):
    def bind(
        self, command: NativeNodeCommand, *, task: Mapping[str, Any]
    ): ...
