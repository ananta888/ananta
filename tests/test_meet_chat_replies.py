"""Reply reservations reach the real Hub task port, never a parallel scheduler."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, select

from agent.repositories.meet_chat_dispatches import SqlChatDispatches, dispatches
from agent.repositories.meet_chat_reservations import SqlChatReservations
from agent.services.meet_chat_admission import MeetChatAdmissionService
from agent.services.meet_chat_reply_service import MeetChatReplyService
from agent.services.meet_contract import MeetError
from agent.services.meet_turn_service import HubMediaTasks, MeetTurnService
from tests.test_meet_chat_admission import NOW, raw, session
from tests.test_meet_media import result

pytestmark = pytest.mark.timeout(45)
PRINCIPAL = SimpleNamespace(subject_id="actor", tenant_id="tenant")


@pytest.fixture
def runtime(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'reply.sqlite'}", connect_args={"timeout": 3})
    reservations, delivery = SqlChatReservations(engine), SqlChatDispatches(engine)
    reservations.initialize()
    delivery.initialize()
    authority, worker, tasks, binding = Mock(), Mock(), Mock(), Mock()
    authority.current.return_value = session()
    tasks.finish.return_value = True
    worker.execute.side_effect = lambda turn: result() | {
        "task_id": turn["task_id"],
        "lease_id": turn["lease_id"],
        "usage": {"input_tokens": 50, "output_tokens": 8},
    }
    admission = MeetChatAdmissionService(authority, reservations, clock=lambda: NOW / 1000).admit(raw())
    service = MeetChatReplyService(authority, delivery, binding, worker, tasks, clock=lambda: NOW / 1000)
    yield SimpleNamespace(
        service=service,
        admission=admission,
        authority=authority,
        worker=worker,
        tasks=tasks,
        binding=binding,
        engine=engine,
        delivery=delivery,
    )
    engine.dispose()


def test_one_reservation_materializes_one_bounded_response(runtime):
    reply = runtime.service.execute(PRINCIPAL, runtime.admission)
    assert reply["reply_to"] == runtime.admission.reservation.message_id
    assert reply["intent_id"] == runtime.admission.reservation.intent_id
    assert reply["published"] is False
    turn = runtime.worker.execute.call_args.args[0]
    assert "meeting" not in turn and "hub_chat_binding" not in turn
    assert turn["response_limits"] == {"max_output_tokens": 128, "max_reply_chars": 450}
    assert turn["deadline"] <= session().scope.deadline_ms / 1000
    task_turn = runtime.tasks.start.call_args.args[0]
    assert task_turn["hub_chat_binding"]["session_id"] == "test-session"
    with pytest.raises(MeetError, match="already_dispatched"):
        runtime.service.execute(PRINCIPAL, runtime.admission)
    runtime.worker.execute.assert_called_once()
    runtime.tasks.start.assert_called_once()


@pytest.mark.parametrize(
    "change",
    [
        {"intent_id": "unknown"},
        {"message_id": "forged"},
        {"sender_peer_id": "other"},
    ],
)
def test_caller_cannot_replace_reserved_identity_or_correlation(runtime, change):
    admission = replace(runtime.admission, reservation=replace(runtime.admission.reservation, **change))
    with pytest.raises(MeetError, match="reservation_mismatch"):
        runtime.service.execute(PRINCIPAL, admission)
    runtime.worker.execute.assert_not_called()
    runtime.tasks.start.assert_not_called()


@pytest.mark.parametrize(
    "scope_change",
    [
        {"tenant_id": "other"},
        {"project_id": "other"},
        {"runtime_id": "other"},
        {"task_id": "other"},
        {"lease_id": "other"},
        {"generation": 2},
        {"membership_epoch": 2},
        {"policy_revision": 2},
        {"origin": "https://other.example.test"},
        {"room_id": "room-222222222222222222"},
    ],
)
def test_current_scope_is_exact_before_claim(runtime, scope_change):
    runtime.authority.current.return_value = session(**scope_change)
    with pytest.raises(MeetError, match="authority_changed"):
        runtime.service.execute(PRINCIPAL, runtime.admission)
    runtime.worker.execute.assert_not_called()


@pytest.mark.parametrize(
    "failure", ["revoke", "stale_task", "stale_lease", "usage", "text", "error", "cancelled", "publish"]
)
def test_failures_do_not_disclose_media_or_enable_retry(runtime, failure):
    def execute(turn):
        response = result() | {
            "task_id": turn["task_id"],
            "lease_id": turn["lease_id"],
            "usage": {"input_tokens": 50, "output_tokens": 8},
        }
        if failure == "revoke":
            runtime.authority.current.return_value = None
        if failure == "stale_task":
            response["task_id"] = "stale"
        if failure == "stale_lease":
            response["lease_id"] = "stale"
        if failure == "usage":
            response["usage"]["output_tokens"] = 129
        if failure == "text":
            response["text"] = "x" * 451
        if failure == "error":
            raise MeetError("meet_worker_unavailable", 503)
        if failure == "cancelled":
            runtime.tasks.finish.return_value = False
        if failure == "publish":
            response["meeting"] = {
                "room_id": session().scope.room_id,
                "status": "published",
                "delivery_verified": False,
            }
        return response

    runtime.worker.execute.side_effect = execute
    with pytest.raises(MeetError):
        runtime.service.execute(PRINCIPAL, runtime.admission)
    with runtime.engine.connect() as connection:
        assert connection.execute(select(dispatches.c.state)).scalar_one() == "failed"
    runtime.authority.current.return_value = session()
    with pytest.raises(MeetError, match="already_dispatched"):
        runtime.service.execute(PRINCIPAL, runtime.admission)
    runtime.worker.execute.assert_called_once()


def test_independent_claimants_cannot_create_two_tasks(runtime):
    barrier = Barrier(2)

    def execute(_):
        barrier.wait(timeout=5)
        try:
            return runtime.service.execute(PRINCIPAL, runtime.admission)["schema"]
        except MeetError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(execute, range(2)))
    assert sorted(outcomes) == ["ananta.meet-chat-reply.v1", "meet_chat_already_dispatched"]
    runtime.tasks.start.assert_called_once()


def test_revocation_at_completion_still_hides_reply(runtime):
    def finish(*_):
        runtime.authority.current.return_value = None
        return True

    runtime.tasks.finish.side_effect = finish
    with pytest.raises(MeetError, match="authority_changed"):
        runtime.service.execute(PRINCIPAL, runtime.admission)


def test_real_hub_task_is_content_free_and_cannot_use_legacy_publication_lease(runtime, app):
    from agent.repository import task_repo

    runtime.service.tasks = HubMediaTasks()
    old_execute = runtime.worker.execute.side_effect
    manual = MeetTurnService(
        runtime.binding, runtime.worker, HubMediaTasks(), [("tenant", "project")], clock=lambda: NOW / 1000
    )

    def execute(turn):
        stored = task_repo.get_by_id(turn["task_id"])
        assert stored.status == "in_progress"
        assert stored.task_kind == "meet_media_turn"
        context = stored.worker_execution_context["meet_media"]
        assert context["chat_reply"]["intent_id"] == runtime.admission.reservation.intent_id
        assert context["response_limits"] == turn["response_limits"]
        assert manual.lease_allowed(turn["task_id"], turn["lease_id"]) is False
        return old_execute(turn)

    runtime.worker.execute.side_effect = execute
    with app.app_context():
        reply = runtime.service.execute(PRINCIPAL, runtime.admission)
        stored = task_repo.get_by_id(reply["media"]["task_id"])
        assert stored.status == "completed"
        assert runtime.admission.text not in json.dumps(stored.model_dump(), default=str)
        assert reply["media"]["text"] not in json.dumps(stored.model_dump(), default=str)
