"""Actual control-Task/project scope for explicitly configured Native task families."""

from collections.abc import Mapping
from typing import Any, Protocol

from agent.services.native_context_snapshot import bounded_context_identifier
from agent.services.native_control_task_scope import native_control_task_scope


class NativeProjectTaskScopePort(Protocol):
    def prepare(self, *, command) -> dict[str, str]: ...


class NativeScopeTaskReader(Protocol):
    def get_by_id(self, task_id: str) -> Any: ...


class NativeScopeProjectReader(Protocol):
    def get(self, tenant_id: str, project_id: str) -> Any: ...


class NativeProjectTaskScopePreparation:
    def __init__(self, *, tasks: NativeScopeTaskReader, projects: NativeScopeProjectReader,
                 task_kinds: frozenset[str], reason_prefix: str):
        self._tasks, self._projects = tasks, projects
        self._kinds, self._prefix = task_kinds, reason_prefix

    def prepare(self, *, command):
        command.assert_valid()
        if command.node.task_kind not in self._kinds:
            raise ValueError(self._prefix + '_kind_invalid')
        parent_id = bounded_context_identifier(command.control_task_id)
        parent = self._tasks.get_by_id(parent_id)
        if parent is None:
            raise ValueError(self._prefix + '_control_task_required')
        raw = dict(parent) if isinstance(parent, Mapping) else parent.model_dump()
        scope = native_control_task_scope(raw, command=command)
        project = self._projects.get(command.tenant_id, scope['project_id'])
        if (project is None or project.tenant_id != command.tenant_id
                or project.project_id != scope['project_id'] or project.status != 'active'):
            raise ValueError(self._prefix + '_project_not_active')
        return {**scope, 'parent_task_id': parent_id}
