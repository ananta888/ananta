"""Real isolated Task aggregate; phase metadata does not own execution or stop."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from agent.repositories.meet_dialog_phases import TaskDialogPhases
from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_phases import MeetDialogPhases
from tests.test_meet_dialog_avatar_negotiation import system

pytestmark = pytest.mark.timeout(45)


def setup(*, reconnect=False, media_timing=False):
    from agent.services.task_runtime_service import compare_and_set_local_task_status

    f = system()
    f.store = TaskDialogPhases(f.tasks, task_status_cas=compare_and_set_local_task_status)
    f.phases = MeetDialogPhases(f.store, f.f.authority, f.meet, clock=lambda: f.f.now)
    f.service.phases = f.phases
    f.service.media_timing = media_timing
    if reconnect:
        f.service.recovery = Mock()
    f.started = f.service.start(f.principal, "project", f.payload)
    f.task_id = f.started["task_id"]
    f.context = f.tasks.get_by_id(f.task_id).worker_execution_context["meet_dialog"]
    f.ids = f.task_id, f.context["lease_id"], f.context["runtime_id"]
    f.scope = f.f.authority.current(*f.ids)
    f.state = {
        "lease": {"sessionId": "ms_" + "a" * 32, "expiresAt": int((f.f.now + 120) * 1000)},
        "peerId": "a" * 16,
        "publicationRevision": 1,
        "publications": [{"publicationId": "camera", "source": "camera", "publicationEpoch": 1}],
    }
    f.meet.observe.return_value = f.state
    return f


def inspect(f, refresh=False):
    return f.phases.inspect(f.principal, "project", f.task_id, refresh=refresh)


@pytest.mark.parametrize("reconnect", [False, True])
def test_negotiated_timing_survives_phase_start_join_and_restart_but_cannot_be_removed(app, reconnect):
    from dataclasses import replace

    with app.app_context():
        f = setup(reconnect=reconnect, media_timing=True)
        assert inspect(f)["phase"] == "connecting"
        assert f.scope.media_timing is True
        f.phases.advance(f.scope, "joined", f.state)
        f.phases = MeetDialogPhases(f.store, f.f.authority, f.meet, clock=lambda: f.f.now)
        assert inspect(f, True)["phase"] == "publishing"
        with pytest.raises(MeetError, match="phase_conflict"):
            f.phases.advance(replace(f.scope, media_timing=False), "joined", f.state)
        assert f.worker.start_dialog.call_count == 1


def test_new_task_persists_phases_and_restarted_coordinator_retains_terminal_precedence(app):
    with app.app_context():
        f = setup()
        assert inspect(f)["phase"] == "connecting" and inspect(f)["revision"] == 3
        f.meet.observe.assert_not_called()
        f.phases.advance(f.scope, "joined", f.state)
        assert inspect(f)["phase"] == "joined"
        publishing = inspect(f, True)
        assert publishing["phase"] == "publishing" and publishing["registered_sources"] == ["camera"]
        assert publishing["observation_fresh"]
        f.phases.advance(f.scope, "joined", f.state)
        assert inspect(f) == publishing  # The ordinary exchange cannot imply source stop.
        assert f.service.inspect(f.principal, "project", f.task_id, stop=True)["status"] == "cancelled"
        f.phases = MeetDialogPhases(f.store, f.f.authority, f.meet, clock=lambda: f.f.now)
        terminal = inspect(f)
        assert terminal["phase"] == "cancelled" and terminal["revision"] == publishing["revision"] + 2
        assert not terminal["observation_fresh"]
        with pytest.raises(MeetError, match="inactive"):
            inspect(f, True)
        with pytest.raises(MeetError, match="inactive"):
            f.phases.advance(f.scope, "joined", f.state)
        assert f.worker.start_dialog.call_count == 1
        history = f.tasks.get_by_id(f.task_id).history
        phase_events = [event for event in history if event.get("event_type") == "meet_dialog_phase_observed"]
        assert [e["details"]["phase"] for e in phase_events] == [
            "admitted",
            "connecting",
            "joined",
            "publishing",
            "stopping",
        ]


@pytest.mark.parametrize("terminal", ["completed", "failed", "cancelled"])
def test_existing_finish_or_deadline_path_needs_no_second_terminal_phase_write(app, terminal):
    with app.app_context():
        f = setup()
        assert f.tasks.finish_bound(*f.ids, terminal)
        assert inspect(f)["phase"] == terminal and inspect(f)["revision"] == 4
        assert f.tasks.get_by_id(f.task_id).worker_execution_context["meet_phase"]["phase"] == "connecting"


def test_record_failure_cannot_obstruct_cancellation_or_trigger_redispatch(app):
    with app.app_context():
        f = setup()
        f.store.task_status_cas = Mock(side_effect=RuntimeError("private database failure"))
        with pytest.raises(MeetError, match="^meet_dialog_phase_storage_unavailable$"):
            f.service.inspect(f.principal, "project", f.task_id, stop=True)
        assert f.tasks.get_by_id(f.task_id).status == "cancelled"
        assert f.worker.start_dialog.call_count == 1


@pytest.mark.parametrize("race", ["controls", "terminal", "runtime", "phase"])
def test_captured_phase_cas_cannot_overwrite_concurrent_task_mutation(app, race):
    from agent.models.meet_dialog_phase import transition
    from agent.services.task_runtime_service import compare_and_set_local_task_status

    with app.app_context():
        f = setup()
        task, record = f.phases._read(f.task_id)
        changed = transition(record, "stopping", int(f.f.now * 1000))
        if race == "controls":
            f.service.control(
                f.principal,
                "project",
                f.task_id,
                {"expected_revision": 1, "chat": False, "audio": False, "screen": False, "avatar": True},
            )
        elif race == "terminal":
            f.tasks.finish_bound(*f.ids, "failed")
        elif race == "phase":
            f.phases.advance(f.scope, "joined", f.state)
        else:
            context = deepcopy(task.worker_execution_context)
            context["meet_dialog"]["runtime_id"] = "replacement"
            assert compare_and_set_local_task_status(
                f.task_id, "in_progress", expected_statuses={"in_progress"}, worker_execution_context=context
            )
        before = f.tasks.get_by_id(f.task_id)
        assert not f.store.replace(task, changed)
        after = f.tasks.get_by_id(f.task_id)
        assert after.worker_execution_context == before.worker_execution_context and after.status == before.status


@pytest.mark.parametrize("race", ["controls", "terminal", "scope"])
def test_observation_in_flight_cannot_overwrite_newer_context(app, race):
    from agent.services.task_runtime_service import compare_and_set_local_task_status

    with app.app_context():
        f = setup()
        f.phases.advance(f.scope, "joined", f.state)

        def observe(*_ids):
            if race == "controls":
                f.service.control(
                    f.principal,
                    "project",
                    f.task_id,
                    {"expected_revision": 1, "chat": False, "audio": False, "screen": False, "avatar": True},
                )
            elif race == "terminal":
                f.tasks.finish_bound(*f.ids, "cancelled")
            else:
                context = deepcopy(f.tasks.get_by_id(f.task_id).worker_execution_context)
                context["meet_dialog"]["runtime_id"] = "replacement"
                compare_and_set_local_task_status(
                    f.task_id, "in_progress", expected_statuses={"in_progress"}, worker_execution_context=context
                )
            return f.state

        f.meet.observe.side_effect = observe
        with pytest.raises(MeetError, match="conflict"):
            inspect(f, True)
        assert f.tasks.get_by_id(f.task_id).worker_execution_context["meet_phase"]["phase"] == "joined"


def test_legacy_task_keeps_exchange_and_stop_without_invented_phase_history(app):
    with app.app_context():
        f = system()
        started = f.service.start(f.principal, "project", f.payload)
        from agent.services.task_runtime_service import compare_and_set_local_task_status

        store = TaskDialogPhases(f.tasks, task_status_cas=compare_and_set_local_task_status)
        f.service.phases = MeetDialogPhases(store, f.f.authority, f.meet, clock=lambda: f.f.now)
        task = f.tasks.get_by_id(started["task_id"])
        context = task.worker_execution_context["meet_dialog"]
        scope = f.f.authority.current(task.id, context["lease_id"], context["runtime_id"])
        f.service.phases.advance(scope, "joined", {})
        with pytest.raises(MeetError, match="phase_unavailable"):
            f.service.phases.inspect(f.principal, "project", task.id)
        assert f.service.inspect(f.principal, "project", task.id, stop=True)["status"] == "cancelled"
        assert "meet_phase" not in f.tasks.get_by_id(task.id).worker_execution_context


@pytest.mark.parametrize("field", ["tenant_id", "project_id", "subject_id"])
def test_foreign_caller_cannot_read_or_refresh_phase(app, field):
    from dataclasses import replace

    with app.app_context():
        f = setup()
        principal = replace(f.principal, **{field: "foreign"})
        project = "foreign" if field == "project_id" else "project"
        with pytest.raises((MeetError, PermissionError)):
            f.phases.inspect(principal, project, f.task_id, refresh=True)
        f.meet.observe.assert_not_called()


def test_current_authority_denial_and_not_joined_never_query_meet(app):
    with app.app_context():
        f = setup()
        with pytest.raises(MeetError, match="not_joined"):
            inspect(f, True)
        f.f.authority.binding.require_write_access = Mock(side_effect=PermissionError("revoked"))
        with pytest.raises(PermissionError):
            inspect(f)
        f.meet.observe.assert_not_called()


def test_failed_initial_phase_storage_closes_new_task_before_worker_dispatch(app):
    from agent.repositories.meet_dialog_phases import TaskDialogPhases

    with app.app_context():
        f = system()
        f.service.phases = MeetDialogPhases(
            TaskDialogPhases(f.tasks, task_status_cas=Mock(return_value=False)),
            f.f.authority,
            f.meet,
            clock=lambda: f.f.now,
        )
        with pytest.raises(MeetError, match="phase_conflict"):
            f.service.start(f.principal, "project", f.payload)
        f.worker.start_dialog.assert_not_called()
        f.issuer.issue_dialog.assert_not_called()
        from sqlmodel import Session, select

        from agent.database import engine
        from agent.db_models import TaskDB

        with Session(engine) as session:
            tasks = session.exec(select(TaskDB).where(TaskDB.task_kind == "meet_dialog_session")).all()
        assert len(tasks) == 1 and tasks[0].status == "failed"


@pytest.mark.parametrize("replacement", ["lease_id", "runtime_id"])
def test_stale_stop_phase_cannot_touch_a_replacement_dispatch(app, replacement):
    with app.app_context():
        f = setup()
        ids = list(f.ids)
        ids[1 if replacement == "lease_id" else 2] = "stale"
        before = f.tasks.get_by_id(f.task_id).worker_execution_context
        with pytest.raises(MeetError, match="phase_conflict"):
            f.phases.stopping(*ids)
        assert f.tasks.get_by_id(f.task_id).worker_execution_context == before


@pytest.mark.parametrize("changed", ["owner_subject", "room_id", "deadline", "capabilities"])
def test_scope_snapshot_cannot_promote_different_immutable_binding(app, changed):
    from dataclasses import replace

    with app.app_context():
        f = setup()
        value = (
            f.scope.deadline - 1
            if changed == "deadline"
            else ("screen.publish",)
            if changed == "capabilities"
            else "stale"
        )
        scope = replace(f.scope, **{changed: value})
        with pytest.raises(MeetError, match="phase_conflict"):
            f.phases.advance(scope, "joined", f.state)
        assert inspect(f)["phase"] == "connecting"
