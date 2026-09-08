"""A generic Task retry must never revive an old Meet dispatch/runtime identity."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from agent.services.meet_contract import MeetError
from tests.test_meet_dialog_avatar_negotiation import system


@pytest.mark.timeout(45)
def test_generic_task_status_cas_cannot_reopen_a_cancelled_meet_dispatch(app):
    from agent.services.task_runtime_service import compare_and_set_local_task_status

    with app.app_context():
        f = system()
        started = f.service.start(f.principal, "project", f.payload)
        task = f.tasks.get_by_id(started["task_id"])
        context = task.worker_execution_context["meet_dialog"]
        ids = task.id, context["lease_id"], context["runtime_id"]
        assert f.tasks.finish_bound(*ids, "cancelled")
        assert not compare_and_set_local_task_status(task.id, "in_progress", expected_statuses={"cancelled"})
        with pytest.raises(MeetError, match="inactive"):
            f.f.authority.current(*ids)


@pytest.mark.parametrize(
    "status", ["completed", "failed", "cancelled", "verification_failed", "skipped", "aborted", "timeout", "archived"]
)
@pytest.mark.parametrize("target", ["todo", "in_progress", "assigned", "paused"])
def test_closed_terminal_status_matrix(status, target):
    from agent.common.meet_task_write_validation import terminal_meet_write_allowed

    row = SimpleNamespace(task_kind="meet_dialog_session", status=status)
    assert not terminal_meet_write_allowed(row, SimpleNamespace(task_kind=row.task_kind, status=target))
    assert terminal_meet_write_allowed(row, deepcopy(row))
    ordinary = SimpleNamespace(task_kind="ordinary", status=status)
    assert terminal_meet_write_allowed(ordinary, SimpleNamespace(task_kind="ordinary", status=target))


@pytest.mark.timeout(45)
@pytest.mark.parametrize(
    "field",
    [
        "status",
        "task_kind",
        "worker_execution_context",
        "tenant_id",
        "project_id",
        "assigned_agent_url",
        "parent_task_id",
    ],
)
def test_terminal_save_cannot_replace_identity_but_metadata_edit_remains_compatible(app, field):
    from agent.services.repository_registry import get_repository_registry

    with app.app_context():
        f = system()
        started = f.service.start(f.principal, "project", f.payload)
        task = f.tasks.get_by_id(started["task_id"])
        context = task.worker_execution_context["meet_dialog"]
        assert f.tasks.finish_bound(task.id, context["lease_id"], context["runtime_id"], "cancelled")
        repos = get_repository_registry()
        candidate = repos.task_repo.get_by_id(task.id)
        setattr(
            candidate,
            field,
            {} if field == "worker_execution_context" else "in_progress" if field == "status" else "foreign",
        )
        with pytest.raises(ValueError, match="^meet_dialog_terminal_identity_immutable$"):
            repos.task_repo.save(candidate)
        stored = repos.task_repo.get_by_id(task.id)
        assert stored.status == "cancelled" and stored.worker_execution_context == task.worker_execution_context
        stored.title = "Synthetic metadata-only edit"
        assert repos.task_repo.save(stored).title == stored.title


@pytest.mark.timeout(45)
def test_force_cannot_reopen_terminal_session_and_active_kind_cannot_be_renamed(app):
    from agent.services.repository_registry import get_repository_registry
    from agent.services.task_runtime_service import compare_and_set_local_task_status

    with app.app_context():
        f = system()
        started = f.service.start(f.principal, "project", f.payload)
        task = f.tasks.get_by_id(started["task_id"])
        candidate = get_repository_registry().task_repo.get_by_id(task.id)
        candidate.task_kind = "ordinary"
        with pytest.raises(ValueError, match="terminal_identity_immutable"):
            get_repository_registry().task_repo.save(candidate)
        context = task.worker_execution_context["meet_dialog"]
        assert f.tasks.finish_bound(task.id, context["lease_id"], context["runtime_id"], "cancelled")
        assert not compare_and_set_local_task_status(
            task.id, "in_progress", expected_statuses={"cancelled"}, force=True
        )
        assert f.tasks.get_by_id(task.id).status == "cancelled"


@pytest.mark.timeout(45)
def test_archived_terminal_session_restores_only_as_terminal_and_legacy_archived_cannot_be_promoted(app):
    from agent.db_models import ArchivedTaskDB, TaskDB, archive_task_record
    from agent.services.repository_registry import get_repository_registry
    from agent.services.task_admin_service import TaskAdminService

    with app.app_context():
        repos = get_repository_registry()
        for status in ("cancelled", "archived"):
            task = TaskDB(id="synthetic-meet-archive-" + status, task_kind="meet_dialog_session", status=status)
            repos.archived_task_repo.save(ArchivedTaskDB(**archive_task_record(task).model_dump()))
            if status == "cancelled":
                assert TaskAdminService().restore_task(task_id=task.id)
                assert repos.task_repo.get_by_id(task.id).status == "cancelled"
            else:
                with pytest.raises(ValueError, match="terminal_identity_immutable"):
                    TaskAdminService().restore_task(task_id=task.id)
                assert repos.task_repo.get_by_id(task.id) is None
                assert repos.archived_task_repo.get_by_id(task.id).status == "archived"


@pytest.mark.parametrize("old,new", [("meet_dialog_session", "ordinary"), ("ordinary", "meet_dialog_session")])
def test_existing_task_kind_cannot_be_used_as_a_rename_pivot(old, new):
    from agent.common.meet_task_write_validation import terminal_meet_write_allowed

    assert not terminal_meet_write_allowed(
        SimpleNamespace(task_kind=old, status="in_progress"), SimpleNamespace(task_kind=new, status="in_progress")
    )
