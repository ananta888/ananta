"""Atomic birth of one delegated Task under its Hub run-control lease."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from sqlmodel import Session, select

from agent.common.canonical_serialization import canonical_json
from agent.db_models import TaskDB
from agent.repositories.task_repository_session import task_repository_session


def insert_native_task_fenced(
    *,
    engine,
    task,
    native_command: Mapping[str, Any],
    lease_guard: Callable[[Session], None],
    prepare_new,
):
    """No independent queue or commit: lease authority and Task share one DB.

    The service validated the task's binding to the lease (``native_command`` is that binding) and passes
    ``lease_guard``, which locks and re-validates the lease inside this transaction. An existing task is
    read-only and accepted only for the exact immutable delegation. No task status, grant or assignment is
    reconstructed here.
    """
    with task_repository_session(engine, write=True) as session:
        lease_guard(session)
        statement = select(TaskDB).where(TaskDB.id == task.id)
        if engine.dialect.name == "postgresql":
            statement = statement.with_for_update()
        existing = session.exec(statement).one_or_none()
        if existing is not None:
            stored = (existing.worker_execution_context or {}).get("native_node_command")
            if existing.tenant_id != task.tenant_id or canonical_json(stored) != canonical_json(dict(native_command)):
                raise ValueError("native_hub_task_id_conflict")
            return existing, False
        original_context = canonical_json(task.worker_execution_context)
        prepared = prepare_new(task, session=session)
        if canonical_json(prepared.worker_execution_context) != original_context:
            raise ValueError("bpmn_fenced_task_policy_binding_changed")
        session.add(prepared)
        session.flush()
        # The anchor lock remains held through this commit; a successor's lease
        # cannot become current between validation and Task visibility.
        session.commit()
        session.refresh(prepared)
        return prepared, True
