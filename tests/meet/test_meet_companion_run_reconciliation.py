"""Hub recovery over actual SQL Tasks and Registry, synthetic model input only."""

import pytest
from sqlmodel import Session, SQLModel

from agent.db_models.tasks import TaskDB
from agent.repositories.meet_companion_run_cleanup import SqlCompanionRunCleanup
from agent.services.meet_companion_run_cleanup import MeetCompanionRunCleanup
from agent.services.meet_companion_run_reconciliation import MeetCompanionRunReconciliation
from tests.meet.test_meet_companion_model_runs import models as models
from tests.meet.test_meet_companion_model_runs import reserve, state
from tests.meet.test_meet_companion_task_grants import assigned as assigned
from tests.meet.test_meet_companion_task_tools import hub as hub


def runner(models, status):
    f, _service = models
    SQLModel.metadata.create_all(f.database)
    with Session(f.database) as session:
        session.add(TaskDB(id="task", title="Synthetic recovery Task", task_kind="meet_dialog_session",
            tenant_id="tenant", project_id="project", status=status,
            worker_execution_context=f.f.task.worker_execution_context))
        session.commit()
    repository = SqlCompanionRunCleanup(f.database)
    return MeetCompanionRunReconciliation(repository, MeetCompanionRunCleanup(repository, f.registry))


@pytest.mark.parametrize("status,expected", [("failed", "failed"), ("completed", "failed"),
                                             ("cancelled", "cancelled")])
def test_terminal_task_without_worker_finish_is_recovered(models, status, expected):
    f, _service = models
    pending = reserve(models)
    service = runner(models, status)
    assert service.run_once() == {"scanned": 1, "settled": 1, "failed": 0}
    assert state(f, pending["run"]["run_id"])["state"] == expected
    assert service.run_once() == {"scanned": 0, "settled": 0, "failed": 0}
    assert service.cursor is None


def test_live_task_is_preserved_until_authoritative_terminal_status(models):
    f, _service = models
    pending = reserve(models)
    service = runner(models, "in_progress")
    assert service.run_once()["scanned"] == 0
    assert state(f, pending["run"]["run_id"])["state"] == "reserved"
    with Session(f.database) as session:
        task = session.get(TaskDB, "task")
        task.status = "failed"
        session.add(task)
        session.commit()
    assert service.run_once()["settled"] == 1


def test_bounded_failure_is_counted_and_retried_on_next_cursor_cycle(models, monkeypatch):
    f, _service = models
    pending = reserve(models)
    service = runner(models, "cancelled")
    with monkeypatch.context() as patch:
        def unavailable(*_args, **_kwargs):
            raise RuntimeError("synthetic_persistence_failure")

        patch.setattr(service.cleanup.repository, "terminate", unavailable)
        assert service.run_once(limit=1) == {"scanned": 1, "settled": 0, "failed": 1}
    assert state(f, pending["run"]["run_id"])["state"] == "reserved"
    assert service.run_once(limit=1)["scanned"] == 0
    assert service.run_once(limit=1)["settled"] == 1


def test_shutdown_does_not_mutate_reservations(models):
    f, _service = models
    pending = reserve(models)
    service = runner(models, "cancelled")
    assert service.run_once(stopped=lambda: True) == {"scanned": 0, "settled": 0, "failed": 0}
    assert state(f, pending["run"]["run_id"])["state"] == "reserved"


def test_malformed_persisted_context_is_bounded_and_cursor_advances(models):
    f, _service = models
    pending = reserve(models)
    service = runner(models, "cancelled")
    with Session(f.database) as session:
        task = session.get(TaskDB, "task")
        task.worker_execution_context = {"meet_dialog": []}
        session.add(task)
        session.commit()
    assert service.run_once() == {"scanned": 1, "settled": 0, "failed": 1}
    assert service.cursor == "task"
    assert service.run_once()["scanned"] == 0
    assert state(f, pending["run"]["run_id"])["state"] == "reserved"
