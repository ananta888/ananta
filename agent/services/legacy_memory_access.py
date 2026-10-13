"""Compatibility readers backed exclusively by current Hub grants and hard SQL scope."""

import logging

from agent.services.memory_policy_gateway import MemoryAccessError
from agent.services.memory_runtime import memory_read_context
from agent.services.project_access_authority import ProjectAccessError
from agent.services.result_memory_access import ResultMemoryAccess
from agent.services.task_read_access_service import TaskReadAccessError
from ananta_contracts.memory_access import MemoryScope

logger = logging.getLogger(__name__)


class ScopedResultMemoryReader:
    def __init__(self, repository):
        self._repository = repository

    def get_by_task(self, task_id, **_legacy_options):
        return self._read(task_id, MemoryScope.task)

    def get_by_goal(self, goal_id, **_legacy_options):
        # Resolve owner IDs without loading memory content before authorization.
        from sqlmodel import Session, select

        from agent.db_models import TaskDB
        from agent.repositories.memory import _engine

        with Session(_engine()) as session:
            task_ids = session.exec(select(TaskDB.id).where(TaskDB.goal_id == goal_id).limit(1000)).all()
        rows = []
        seen = set()
        for task_id in task_ids:
            task_rows = self._read(task_id, MemoryScope.task, goal_id=goal_id)
            goal_rows = self._read(task_id, MemoryScope.goal, goal_id=goal_id)
            for row in [*task_rows, *goal_rows]:
                if row.id not in seen:
                    seen.add(row.id)
                    rows.append(row)
                    if len(rows) >= 1000:
                        return rows
        return rows

    def _read(self, task_id, scope, *, goal_id=None):
        try:
            with memory_read_context(task_id) as context:
                access = ResultMemoryAccess(context.gateway)
                decision = access.authorize_read(actor=context.actor, run_id=context.run_id, scope=scope)
                namespace = decision.target.namespace
                if namespace.task_id != task_id or goal_id is not None and namespace.goal_id != goal_id:
                    raise MemoryAccessError("memory_result_binding_mismatch")
                rows = self._repository.get_by_namespace(namespace, scope)
                access.recheck(decision)
                return rows
        except (MemoryAccessError, TaskReadAccessError, ProjectAccessError) as exc:
            logger.info("Memory read blocked: %s", exc.reason_code)
            return []


def scoped_memory_reader(repository):
    return repository if isinstance(repository, ScopedResultMemoryReader) else ScopedResultMemoryReader(repository)
