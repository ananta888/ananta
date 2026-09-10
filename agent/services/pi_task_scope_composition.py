"""Hub-only scope lookup using the existing Task and project repositories."""

from agent.repositories.projects import ProjectRepository
from agent.repositories.task_repository_session import task_repository_session
from agent.services.pi_task_scope_preparation import PiTaskScopePreparationService
from agent.services.repository_registry import get_repository_registry


class HubPiTaskScopePreparer:
    def prepare(self, *, command):
        # Load the Task before opening the project read session; no nested
        # repository session may interfere with a shared SQLite connection.
        task = get_repository_registry().task_repo.get_by_id(command.control_task_id)
        with task_repository_session(_engine()) as session:
            return PiTaskScopePreparationService(
                tasks=_LoadedControlTask(task), projects=ProjectRepository(session),
            ).prepare(command=command)


class _LoadedControlTask:
    def __init__(self, task):
        self._task = task

    def get_by_id(self, task_id):
        return self._task if self._task is not None and self._task.id == task_id else None


def _engine():
    from agent.database import engine

    return engine
