"""Bind production Pi tasks to an actual active Hub control-Task project."""

from collections.abc import Mapping
from typing import Any, Protocol

from agent.services.native_context_snapshot import bounded_context_identifier
from agent.services.native_control_task_scope import native_control_task_scope
from agent.services.workflow_runtime.native_graph_contracts import NativeNodeCommand


class PiTaskScopePreparationPort(Protocol):
    def prepare(self, *, command: NativeNodeCommand) -> dict[str, str]: ...


class PiScopeTaskReader(Protocol):
    def get_by_id(self, task_id: str) -> Any: ...


class PiScopeProjectReader(Protocol):
    def get(self, tenant_id: str, project_id: str) -> Any: ...


class PiTaskScopePreparationService:
    def __init__(self, *, tasks: PiScopeTaskReader, projects: PiScopeProjectReader):
        self._tasks, self._projects = tasks, projects

    def prepare(self, *, command: NativeNodeCommand) -> dict[str, str]:
        command.assert_valid()
        if command.node.task_kind != "pi_coding_agent":
            raise ValueError("pi_task_scope_kind_invalid")
        parent_id = bounded_context_identifier(command.control_task_id)
        parent = self._tasks.get_by_id(parent_id)
        if parent is None:
            raise ValueError("pi_task_scope_control_task_required")
        raw = dict(parent) if isinstance(parent, Mapping) else parent.model_dump()
        scope = native_control_task_scope(raw, command=command)
        project = self._projects.get(command.tenant_id, scope["project_id"])
        if (
            project is None or project.tenant_id != command.tenant_id
            or project.project_id != scope["project_id"] or project.status != "active"
        ):
            raise ValueError("pi_task_scope_project_not_active")
        return {**scope, "parent_task_id": parent_id}
