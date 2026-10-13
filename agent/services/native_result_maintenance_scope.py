"""Hub composition of real control-Task and project scope for owner maintenance."""

import time

from agent.repositories.projects import ProjectRepository
from agent.repositories.task_repository_session import task_repository_session
from agent.services.native_project_task_scope import NativeProjectTaskScopePreparation
from agent.services.native_result_maintenance_owner import require_registered_result_store_owner
from agent.services.repository_registry import get_repository_registry
from ananta_contracts.native_result_maintenance import MAINTENANCE_TASK_KIND, maintenance_input


class HubResultMaintenanceScopePreparer:
    def __init__(self, *, clock=time.time, offline_timeout=300):
        self._clock, self._offline_timeout = clock, offline_timeout

    def prepare(self, *, command):
        _, owner = maintenance_input(command.input_data.get('workflow_input'))
        task = get_repository_registry().task_repo.get_by_id(command.control_task_id)
        with task_repository_session(_engine()) as session:
            scope = NativeProjectTaskScopePreparation(
                tasks=_LoadedControlTask(task), projects=ProjectRepository(session),
                task_kinds=frozenset({MAINTENANCE_TASK_KIND}),
                reason_prefix='native_result_maintenance_scope').prepare(command=command)
        if owner is not None:
            worker = get_repository_registry().agent_repo.get_by_url(owner['worker_url'])
            require_registered_result_store_owner(
                worker, owner, now=self._clock(), offline_timeout=self._offline_timeout)
            scope['assigned_agent_url'] = owner['worker_url']
        return scope


class _LoadedControlTask:
    def __init__(self, task):
        self._task = task

    def get_by_id(self, task_id):
        return self._task if self._task is not None and self._task.id == task_id else None


def _engine():
    from agent.database import engine

    return engine
