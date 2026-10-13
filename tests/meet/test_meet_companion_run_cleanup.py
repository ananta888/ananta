"""Actual SQL/Registry cleanup with synthetic Task and provider fixtures."""

import pytest

from agent.repositories.meet_companion_run_cleanup import SqlCompanionRunCleanup
from agent.services.meet_companion_run_cleanup import MeetCompanionRunCleanup
from agent.services.meet_dialog_tasks import HubDialogTasks
from tests.meet.test_meet_companion_model_runs import (
    finish,
    reserve,
    state,
)
from tests.meet.test_meet_companion_model_runs import (
    models as models,
)
from tests.meet.test_meet_companion_task_grants import assigned as assigned
from tests.meet.test_meet_companion_task_tools import hub as hub
from tests.meet.test_meet_companion_task_tools import post


def cleanup(models):
    f, _service = models
    f.f.task.id = "task"
    return MeetCompanionRunCleanup(SqlCompanionRunCleanup(f.database), f.registry, clock=lambda: 1234)


@pytest.mark.parametrize("status,expected", [("completed", "failed"), ("failed", "failed"),
                                             ("cancelled", "cancelled")])
def test_terminal_task_closes_only_pending_model_runs_idempotently(models, status, expected):
    f, _service = models
    pending = reserve(models)
    completed = reserve(models, nonce="c" * 32)
    finish(models, completed)
    successful = state(f, completed["run"]["run_id"])
    tool = post(f)[1].get_json()
    service = cleanup(models)
    f.f.task.status = status
    assert service.terminal(f.f.task, lease_id="dispatch", runtime_id="runtime") == 1
    assert state(f, pending["run"]["run_id"])["state"] == expected
    assert state(f, completed["run"]["run_id"]) == successful
    assert state(f, tool["run"]["run_id"])["state"] == "succeeded"
    assert service.terminal(f.f.task, lease_id="dispatch", runtime_id="runtime") == 0


@pytest.mark.parametrize("status,lease,runtime", [("in_progress", "dispatch", "runtime"),
    ("cancelled", "stale", "runtime"), ("cancelled", "dispatch", "foreign")])
def test_live_task_or_foreign_assignment_cannot_cleanup(models, status, lease, runtime):
    f, _service = models
    pending = reserve(models)
    service = cleanup(models)
    f.f.task.status = status
    assert service.terminal(f.f.task, lease_id=lease, runtime_id=runtime) == 0
    assert state(f, pending["run"]["run_id"])["state"] == "reserved"


def test_successful_result_winning_atomic_cleanup_race_is_preserved(models, monkeypatch):
    f, _service = models
    pending = reserve(models)
    service = cleanup(models)
    original = service.repository.terminate

    def race(identity, **kwargs):
        f.registry.record_result(tenant_id="tenant", project_id="project", run_id=identity.run_id,
            assignment_id="runtime", dispatch_lease_id="dispatch", terminal_state="succeeded", result_digest="a" * 64)
        return original(identity, **kwargs)

    monkeypatch.setattr(service.repository, "terminate", race)
    f.f.task.status = "cancelled"
    assert service.terminal(f.f.task, lease_id="dispatch", runtime_id="runtime") == 0
    assert state(f, pending["run"]["run_id"])["result_digest"] == "a" * 64


def test_repeated_task_finish_retries_cleanup_after_terminal_cas(models, monkeypatch):
    f, _service = models
    pending = reserve(models)
    service = cleanup(models)
    tasks = HubDialogTasks(run_cleanup=service)
    monkeypatch.setattr(tasks, "get_by_id", lambda _task: f.f.task)

    def terminal(_scope, status):
        changed = f.f.task.status == "in_progress"
        if changed:
            f.f.task.status = status
        return changed

    monkeypatch.setattr(tasks, "finish", terminal)
    assert tasks.finish_bound("task", "dispatch", "runtime", "cancelled")
    assert not tasks.finish_bound("task", "dispatch", "runtime", "completed")
    assert f.f.task.status == "cancelled"
    assert state(f, pending["run"]["run_id"])["state"] == "cancelled"


def test_failed_cleanup_keeps_task_cancelled_and_repeated_callback_retries(models, monkeypatch):
    f, _service = models
    pending = reserve(models)
    service = cleanup(models)
    tasks = HubDialogTasks(run_cleanup=service)
    monkeypatch.setattr(tasks, "get_by_id", lambda _task: f.f.task)

    def terminal(_scope, status):
        changed = f.f.task.status == "in_progress"
        if changed:
            f.f.task.status = status
        return changed

    monkeypatch.setattr(tasks, "finish", terminal)
    original = service.repository.terminate
    with monkeypatch.context() as patch:
        def unavailable(*_args, **_kwargs):
            raise RuntimeError("synthetic_database_unavailable")

        patch.setattr(service.repository, "terminate", unavailable)
        with pytest.raises(RuntimeError, match="synthetic_database_unavailable"):
            tasks.finish_bound("task", "dispatch", "runtime", "cancelled")
    assert f.f.task.status == "cancelled"
    assert state(f, pending["run"]["run_id"])["state"] == "reserved"
    assert service.repository.terminate == original
    assert not tasks.finish_bound("task", "dispatch", "runtime", "cancelled")
    assert state(f, pending["run"]["run_id"])["state"] == "cancelled"


def test_result_finishing_before_registry_validation_is_preserved(models, monkeypatch):
    f, _service = models
    pending = reserve(models)
    service = cleanup(models)
    original = f.registry.assignment_projection

    def race(**kwargs):
        f.registry.record_result(tenant_id="tenant", project_id="project", run_id=kwargs["run_id"],
            assignment_id="runtime", dispatch_lease_id="dispatch", terminal_state="succeeded", result_digest="a" * 64)
        return original(**kwargs)

    monkeypatch.setattr(f.registry, "assignment_projection", race)
    f.f.task.status = "cancelled"
    assert service.terminal(f.f.task, lease_id="dispatch", runtime_id="runtime") == 0
    assert state(f, pending["run"]["run_id"])["state"] == "succeeded"


@pytest.mark.parametrize("status,expected", [("completed", "failed"), ("failed", "failed"),
                                             ("cancelled", "cancelled")])
def test_real_sql_task_cas_triggers_registry_cleanup(app, models, status, expected):
    from sqlmodel import Session

    from agent.database import engine
    from agent.db_models import TaskDB
    from tests.meet_dialog_lifecycle_fixture import ensure_project

    f, _service = models
    pending = reserve(models)
    service = cleanup(models)
    with app.app_context():
        ensure_project(engine, tenant="tenant", project="project")
        with Session(engine) as session:
            session.add(TaskDB(id="task", title="Synthetic model cleanup Task", tenant_id="tenant",
                project_id="project", status="in_progress", task_kind="meet_dialog_session",
                worker_execution_context=f.f.task.worker_execution_context))
            session.commit()
        tasks = HubDialogTasks(run_cleanup=service)
        assert not tasks.finish_bound("task", "foreign", "runtime", status)
        assert state(f, pending["run"]["run_id"])["state"] == "reserved"
        assert tasks.finish_bound("task", "dispatch", "runtime", status)
        assert tasks.get_by_id("task").status == status
        assert state(f, pending["run"]["run_id"])["state"] == expected
