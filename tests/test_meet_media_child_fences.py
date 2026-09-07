"""Current child linkage and exact CAS; mutations affect only the test database."""

from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlmodel import Session

from agent.db_models import OrganizationInstanceDB, TaskDB
from agent.services.meet_contract import MeetError
from agent.services.meet_turn_service import HubMediaTasks, MeetTurnService
from tests.test_meet_dialog_audio import runtime
from tests.test_meet_media import turn
from tests.test_meet_media_child_scope import organized_dialog

pytestmark = pytest.mark.timeout(45)


def revoke(engine, task_id, reason):
    with Session(engine) as session:
        if reason == "organization":
            row = session.get(OrganizationInstanceDB, "meet-test-org")
            row.lifecycle = "paused"
        else:
            row = session.get(TaskDB, task_id)
            row.status = "cancelled"
        session.add(row)
        session.commit()


@pytest.mark.parametrize("field", ["parent_task_id", "organization_id", "unit_id", "team_id", "role_slot_id"])
def test_audio_child_scope_cannot_be_erased_after_admission(app, field):
    from agent.database import engine

    with app.app_context():
        f, _, _, audio = organized_dialog(engine)
        job = f.service.audio_coordinator.start(audio)["job"]
        with Session(engine) as session:
            row = session.get(TaskDB, job["task_id"])
            setattr(row, field, None)
            session.add(row)
            session.commit()
        f.meet.inspect.reset_mock()
        with pytest.raises(MeetError):
            f.service.audio_coordinator.current(tuple(audio[k] for k in ("task_id", "lease_id", "runtime_id")), job)
        f.meet.inspect.assert_not_called()


@pytest.mark.parametrize("reason", ["parent", "organization"])
def test_audio_parent_loss_prevents_even_reserving_a_child(app, reason):
    from agent.database import engine

    with app.app_context():
        f, dialog, scope, _ = organized_dialog(engine)
        _, _, audio_service, payload = runtime()
        job = audio_service.start(payload)["job"]
        revoke(engine, dialog.id, reason)
        with pytest.raises(MeetError):
            f.tasks.claim_audio(scope, job, f.f.now)
        assert f.tasks.get_by_id(job["task_id"]) is None
        context = f.tasks.get_by_id(dialog.id).worker_execution_context["meet_dialog"]
        assert context["audio_job"] is None and context["audio_count"] == 0


def mutate_during_cas(monkeypatch, engine, task_id, event):
    from agent.services import task_runtime_service

    compare = task_runtime_service.compare_and_set_local_task_status

    def changed(*args, **kwargs):
        if kwargs.get("event_type") == event:
            with Session(engine) as session:
                row = session.get(TaskDB, task_id)
                row.role_slot_id = None
                session.add(row)
                session.commit()
        return compare(*args, **kwargs)

    monkeypatch.setattr(task_runtime_service, "compare_and_set_local_task_status", changed)


def test_audio_reservation_cas_fences_scope_drift_even_with_unchanged_dispatch_context(app, monkeypatch):
    from agent.database import engine

    with app.app_context():
        f, dialog, scope, _ = organized_dialog(engine)
        _, _, audio_service, payload = runtime()
        job = audio_service.start(payload)["job"]
        mutate_during_cas(monkeypatch, engine, dialog.id, "meet_audio_delegated")
        with pytest.raises(MeetError, match="^meet_audio_job_conflict$"):
            f.tasks.claim_audio(scope, job, f.f.now)
        assert f.tasks.get_by_id(job["task_id"]) is None
        assert f.tasks.get_by_id(dialog.id).worker_execution_context["meet_dialog"]["audio_count"] == 0


@pytest.mark.parametrize("reason", ["parent", "organization"])
def test_revoked_media_cannot_acquire_capacity_complete_or_keep_publication_lease(app, reason):
    from agent.database import engine

    with app.app_context():
        f, dialog, _, _ = organized_dialog(engine)
        tasks = HubMediaTasks()
        request = turn() | {"task_id": str(uuid4()), "binding_task_id": dialog.id}
        tasks.start(request, "owner")
        tasks.require_current(request)
        media = MeetTurnService(f.f.binding, Mock(), tasks, [("tenant", "project")])
        assert media.lease_allowed(request["task_id"], request["lease_id"])
        revoke(engine, dialog.id, reason)
        with pytest.raises(MeetError):
            tasks.require_current(request)
        assert not media.lease_allowed(request["task_id"], request["lease_id"])
        assert not tasks.finish(request, "completed")
        assert tasks.finish(request, "failed")
        assert f.tasks.get_by_id(request["task_id"]).status == "failed"


@pytest.mark.parametrize("reason", ["parent", "organization"])
def test_inactive_parent_prevents_media_ingestion(app, reason):
    from agent.database import engine

    with app.app_context():
        f, dialog, _, _ = organized_dialog(engine)
        request = turn() | {"task_id": str(uuid4()), "binding_task_id": dialog.id}
        revoke(engine, dialog.id, reason)
        with pytest.raises(MeetError):
            HubMediaTasks().start(request, "owner")
        assert f.tasks.get_by_id(request["task_id"]) is None


def test_media_success_cas_fences_scope_change_and_preserves_failure_cleanup(app, monkeypatch):
    from agent.database import engine

    with app.app_context():
        f, dialog, _, _ = organized_dialog(engine)
        tasks = HubMediaTasks()
        request = turn() | {"task_id": str(uuid4()), "binding_task_id": dialog.id}
        tasks.start(request, "owner")
        mutate_during_cas(monkeypatch, engine, request["task_id"], "meet_media_completed")
        assert not tasks.finish(request, "completed")
        assert f.tasks.get_by_id(request["task_id"]).status == "in_progress"
        assert tasks.finish(request, "failed")


def test_uncertain_media_success_lookup_fails_before_terminal_cas(monkeypatch):
    registry = Mock()
    registry.task_repo.get_by_id.side_effect = RuntimeError("private_database_detail")
    monkeypatch.setattr("agent.services.repository_registry.get_repository_registry", lambda: registry)
    compare = Mock(side_effect=AssertionError("uncertain authority must not complete"))
    monkeypatch.setattr("agent.services.task_runtime_service.compare_and_set_local_task_status", compare)
    assert HubMediaTasks().finish(turn(), "completed") is False
    compare.assert_not_called()
