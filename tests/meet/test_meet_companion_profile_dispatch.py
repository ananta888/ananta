"""Closed Companion profile admission over real Hub SQL tasks and dispatch ports."""

from unittest.mock import Mock

import pytest

from agent.services.meet_contract import MeetError
from ananta_contracts.meet_dialog import validate_assignment
from tests.meet.test_meet_dialog_avatar_negotiation import system
from tests.meet.test_meet_dialog_transport import assignment
from tests.meet.test_meet_dialog_worker_router import fixture as router_fixture


def companion_system():
    f = system()
    f.service.companion_execution = True
    f.f.authority.policies[("tenant", "project")] = frozenset({"chat.read", "chat.send", "avatar.publish"})
    f.payload = {"capabilities": ["chat.read", "chat.send"], "duration_seconds": 300,
                 "chat_mode": "room", "companion_execution": True}
    return f


def test_normal_hub_task_admission_persists_exact_companion_profile_before_dispatch(app):
    with app.app_context():
        f = companion_system()
        result = f.service.start(f.principal, "project", f.payload)
        task = f.tasks.get_by_id(result["task_id"])
        context = task.worker_execution_context["meet_dialog"]
        assert context["companion_execution"] is True
        scope = f.f.authority.current(task.id, context["lease_id"], context["runtime_id"])
        assert scope.companion_execution is True
        wire = f.worker.start_dialog.call_args.args[0]
        assert wire["companion_execution"] is True and validate_assignment(wire, f.f.now) == wire
        assert f.service.inspect(f.principal, "project", task.id)["companion_execution"] is True


def test_companion_admission_and_phase_restart_preserve_bound_execution_profile(app):
    from dataclasses import replace

    from agent.repositories.meet_dialog_phases import TaskDialogPhases
    from agent.services.meet_dialog_phases import MeetDialogPhases
    from agent.services.task_runtime_service import compare_and_set_local_task_status

    with app.app_context():
        f = companion_system()
        store = TaskDialogPhases(f.tasks, task_status_cas=compare_and_set_local_task_status)
        phases = MeetDialogPhases(store, f.f.authority, f.meet, clock=lambda: f.f.now)
        f.service.phases = phases
        result = f.service.start(f.principal, "project", f.payload)
        task = f.tasks.get_by_id(result["task_id"])
        context = task.worker_execution_context["meet_dialog"]
        scope = f.f.authority.current(task.id, context["lease_id"], context["runtime_id"])
        state = {"lease": {"sessionId": "ms_" + "a" * 32, "expiresAt": (f.f.now + 120) * 1000},
                 "peerId": "a" * 16, "publicationRevision": 1,
                 "publications": [{"publicationId": "camera", "source": "camera", "publicationEpoch": 1}]}
        assert phases.inspect(f.principal, "project", task.id)["phase"] == "connecting"
        phases.advance(scope, "joined", state)
        restarted = MeetDialogPhases(store, f.f.authority, f.meet, clock=lambda: f.f.now)
        assert restarted.inspect(f.principal, "project", task.id)["phase"] == "joined"
        f.meet.observe.return_value = state
        assert restarted.inspect(f.principal, "project", task.id, refresh=True)["phase"] == "publishing"
        with pytest.raises(MeetError, match="phase_conflict"):
            restarted.advance(replace(scope, companion_execution=False), "joined", state)
        assert f.worker.start_dialog.call_count == 1


@pytest.mark.parametrize("expired", [False, True, "cancel"])
def test_companion_phase_callback_uses_only_verified_hub_observation(app, expired):
    from agent.repositories.meet_dialog_phases import TaskDialogPhases
    from agent.services.meet_dialog_phases import MeetDialogPhases
    from agent.services.task_runtime_service import compare_and_set_local_task_status

    with app.app_context():
        f = companion_system()
        store = TaskDialogPhases(f.tasks, task_status_cas=compare_and_set_local_task_status)
        f.service.phases = MeetDialogPhases(store, f.f.authority, f.meet, clock=lambda: f.f.now)
        result = f.service.start(f.principal, "project", f.payload)
        context = f.tasks.get_by_id(result["task_id"]).worker_execution_context["meet_dialog"]
        payload = {"task_id": result["task_id"], "lease_id": context["lease_id"],
                   "runtime_id": context["runtime_id"], "meet_session_id": "ms_" + "a" * 32, "nonce": "b" * 32}
        f.meet.observe.return_value = {
            "lease": {"sessionId": payload["meet_session_id"], "expiresAt": (f.f.now + 120) * 1000},
            "peerId": "a" * 16, "publicationRevision": 1,
            "publications": [{"publicationId": "camera", "source": "camera", "publicationEpoch": 1}],
        }
        if expired == "cancel":
            state = f.meet.observe.return_value

            def cancelled_during_read(*_ids):
                f.service.inspect(f.principal, "project", result["task_id"], stop=True)
                return state

            f.meet.observe.side_effect = cancelled_during_read
            with pytest.raises(MeetError, match="task_inactive"):
                f.service.observe(payload)
            current = f.tasks.get_by_id(result["task_id"])
            assert current.status == "cancelled"
            assert current.worker_execution_context["meet_phase"]["membership"] is None
            return
        if expired:
            f.meet.observe.return_value["lease"]["expiresAt"] = f.f.now * 1000 - 1
            with pytest.raises(MeetError):
                f.service.observe(payload)
            assert f.service.phases.inspect(f.principal, "project", result["task_id"])["phase"] == "connecting"
            return
        assert f.service.observe(payload) == {"schema": "ananta.meet-dialog-observed.v1", "nonce": payload["nonce"]}
        assert f.service.phases.inspect(f.principal, "project", result["task_id"])["phase"] == "publishing"
        f.meet.observe.side_effect = MeetError("synthetic_membership_revoked", 403)
        with pytest.raises(MeetError, match="membership_revoked"):
            f.service.observe(payload)
        assert f.worker.start_dialog.call_count == 1


def test_profile_is_default_off_and_denied_before_task_or_worker_creation(app):
    with app.app_context():
        f = companion_system()
        f.service.companion_execution = False
        with pytest.raises(MeetError, match="not_enabled"):
            f.service.start(f.principal, "project", f.payload)
        f.worker.start_dialog.assert_not_called()
        f.issuer.issue_dialog.assert_not_called()


@pytest.mark.parametrize("patch", [{"companion_execution": False}, {"companion_execution": 1},
                                   {"avatar_images": True}, {"audio_mode": "dialog"}])
def test_companion_cannot_negotiate_unimplemented_or_receive_profiles(app, patch):
    with app.app_context():
        f = companion_system()
        with pytest.raises(MeetError):
            f.service.start(f.principal, "project", f.payload | patch)
        f.worker.start_dialog.assert_not_called()


@pytest.mark.parametrize("bound,selected", [(False, True), (True, False)])
def test_worker_router_denies_runtime_profile_replacement_or_removal(bound, selected):
    f = router_fixture()
    f.scope.companion_execution = bound
    if selected:
        f.value["companion_execution"] = True
    with pytest.raises(MeetError, match="publisher_binding_denied"):
        f.router.start_dialog(f.value)
    f.first.start_dialog.assert_not_called()
    f.second.start_dialog.assert_not_called()


@pytest.mark.parametrize("companion", [False, True])
def test_executor_selects_only_fixed_installed_module_from_validated_signed_profile(monkeypatch, tmp_path, companion):
    import worker.meet_media.dialog_executor as module

    wire = assignment() | ({"companion_execution": True} if companion else {})
    spawn, thread, channel = Mock(), Mock(), Mock()
    channel.child_options.return_value = {}
    monkeypatch.setattr(module.subprocess, "Popen", spawn)
    monkeypatch.setattr(module.threading, "Thread", thread)
    monkeypatch.setattr(module, "DialogProgressChannel", lambda: channel)
    executor = module.DialogExecutor(tmp_path / "replay.sqlite")
    accepted = executor.start(wire)
    expected = "companion_task_runtime" if companion else "dialog_runtime"
    assert spawn.call_args.args[0][-1] == "worker.meet_media." + expected
    assert accepted["lease_id"] == wire["lease_id"]
    assert spawn.call_args.kwargs["stdin"].closed
    with pytest.raises(ValueError, match="replayed"):
        executor.start(wire)
    assert spawn.call_count == 1
