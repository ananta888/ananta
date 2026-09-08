"""Bound dispatch port preserves returns and exceptions, never tries another Worker."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_worker_router import MeetDialogWorkerRouter
from tests.test_meet_dialog_transport import assignment

FIRST, SECOND = "http://publisher:8091", "http://second:8091"
pytestmark = pytest.mark.timeout(45)


def fixture():
    value = assignment()
    scope = SimpleNamespace(
        **{
            k: value[k]
            for k in (
                "task_id",
                "lease_id",
                "runtime_id",
                "session_id",
                "tenant_id",
                "project_id",
                "deadline",
                "capabilities",
                "audio_mode",
            )
        },
        **{k: value["meeting"][k] for k in ("origin", "room_id")},
        avatar_selection=None,
        voice_selection=None,
        avatar_videos=False,
    )
    task = SimpleNamespace(
        assigned_agent_url=SECOND,
        organization_id="org",
        role_slot_id="slot",
        worker_execution_context={"meet_role_assignment": {"publisher_url": SECOND, "snapshot_digest": "a" * 64}},
    )
    authority, tasks, first, second = Mock(), Mock(), Mock(), Mock()
    authority.current.return_value = scope
    tasks.get_by_id.return_value = task
    router = MeetDialogWorkerRouter(authority, tasks, {FIRST: first, SECOND: second}, FIRST)
    return SimpleNamespace(**locals())


def test_exact_bound_target_not_default_receives_identical_assignment_once():
    f = fixture()
    assert f.router.start_dialog(f.value) is f.second.start_dialog.return_value
    f.first.start_dialog.assert_not_called()
    assert f.second.start_dialog.call_args.args[0] is f.value
    assert f.authority.current.call_count == 2 and f.tasks.get_by_id.call_count == 2


def test_uncertain_dispatch_preserves_exception_without_retry_or_fallback():
    f = fixture()
    failure = RuntimeError("synthetic")
    f.second.start_dialog.side_effect = failure
    with pytest.raises(RuntimeError) as result:
        f.router.start_dialog(f.value)
    assert result.value is failure
    f.second.start_dialog.assert_called_once()
    f.first.start_dialog.assert_not_called()


@pytest.mark.parametrize(
    "field", ["task_id", "lease_id", "runtime_id", "session_id", "tenant_id", "project_id", "deadline", "audio_mode"]
)
def test_assignment_cannot_override_current_scope(field):
    f = fixture()
    f.value[field] = f.value[field] - 1 if field == "deadline" else "transcribe" if field == "audio_mode" else "foreign"
    with pytest.raises((MeetError, ValueError)):
        f.router.start_dialog(f.value)
    f.first.start_dialog.assert_not_called()
    f.second.start_dialog.assert_not_called()


@pytest.mark.parametrize(
    "case", ["unknown", "changed", "missing_role", "malformed_role", "second_read_missing", "second_read_changed"]
)
def test_destination_binding_or_reread_failure_never_sends(case):
    f = fixture()
    if case == "unknown":
        f.task.assigned_agent_url = "http://unknown:8091"
        f.task.worker_execution_context["meet_role_assignment"]["publisher_url"] = f.task.assigned_agent_url
    elif case == "changed":
        f.task.assigned_agent_url = FIRST
    elif case == "missing_role":
        f.task.worker_execution_context.clear()
    elif case == "malformed_role":
        f.task.worker_execution_context["meet_role_assignment"] = []
    else:
        second = None if case == "second_read_missing" else deepcopy(f.task)
        if second is not None:
            second.worker_execution_context["meet_role_assignment"]["snapshot_digest"] = "b" * 64
        f.tasks.get_by_id.side_effect = [f.task, second]
    with pytest.raises(MeetError, match="publisher_binding_denied"):
        f.router.start_dialog(f.value)
    f.first.start_dialog.assert_not_called()
    f.second.start_dialog.assert_not_called()


def test_nonorganization_legacy_uses_only_default():
    f = fixture()
    f.task.organization_id = f.task.role_slot_id = None
    f.task.worker_execution_context.clear()
    assert f.router.start_dialog(f.value) is f.first.start_dialog.return_value
    f.second.start_dialog.assert_not_called()


def test_authority_revoke_prevents_send_and_constructor_copies_destination_map():
    f = fixture()
    f.authority.current.side_effect = [f.scope, MeetError("meet_dialog_assignment_revoked", 403)]
    with pytest.raises(MeetError, match="assignment_revoked"):
        f.router.start_dialog(f.value)
    f.first.start_dialog.assert_not_called()
    f.second.start_dialog.assert_not_called()
    with pytest.raises(TypeError):
        f.router.workers[SECOND] = f.first


@pytest.mark.parametrize("case", ["origin", "room_id", "capabilities", "avatar", "voice", "mutable_destination"])
def test_publication_and_mutable_record_changes_cannot_broaden_dispatch(case):
    f = fixture()
    if case == "origin":
        f.value["meeting"]["origin"] = "https://foreign.example.test"
    elif case == "room_id":
        f.value["meeting"]["room_id"] = "room-" + "b" * 18
    elif case == "capabilities":
        f.value["capabilities"] = ["chat.send"]
    elif case == "avatar":
        f.scope.avatar_selection = {"mode": "neutral-ai-v1"}
    elif case == "voice":
        f.scope.voice_selection = {"mode": "configured-piper-v1"}
    else:

        def revalidate(*ids):
            if f.authority.current.call_count == 2:
                f.task.assigned_agent_url = FIRST
            return f.scope

        f.authority.current.side_effect = revalidate
    with pytest.raises(MeetError, match="publisher_binding_denied"):
        f.router.start_dialog(f.value)
    f.first.start_dialog.assert_not_called()
    f.second.start_dialog.assert_not_called()
