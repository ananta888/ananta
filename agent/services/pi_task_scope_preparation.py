"""Bind production Pi tasks to an actual active Hub control-Task project."""

from typing import Any, Protocol

from agent.services.native_project_task_scope import NativeProjectTaskScopePreparation
from agent.services.workflow_runtime.native_graph_contracts import NativeNodeCommand


class PiTaskScopePreparationPort(Protocol):
    def prepare(self, *, command: NativeNodeCommand) -> dict[str, str]: ...


class PiScopeTaskReader(Protocol):
    def get_by_id(self, task_id: str) -> Any: ...


class PiScopeProjectReader(Protocol):
    def get(self, tenant_id: str, project_id: str) -> Any: ...


class PiTaskScopePreparationService:
    def __init__(self, *, tasks: PiScopeTaskReader, projects: PiScopeProjectReader):
        self._preparation = NativeProjectTaskScopePreparation(tasks=tasks, projects=projects,
            task_kinds=frozenset({'pi_coding_agent'}), reason_prefix='pi_task_scope')

    def prepare(self, *, command: NativeNodeCommand) -> dict[str, str]:
        return self._preparation.prepare(command=command)
