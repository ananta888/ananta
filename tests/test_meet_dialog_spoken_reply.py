"""Real SQL event/dispatch admission, synthetic media result and current policy."""

import copy
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine

from agent.repositories.meet_chat_dispatches import SqlChatDispatches
from agent.repositories.meet_chat_reservations import SqlChatReservations
from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_replies import MeetDialogReplies
from agent.services.meet_dialog_spoken_reply import MeetDialogSpokenReply
from ananta_contracts.meet_speech import speech_profile
from ananta_contracts.meet_spoken_reply import REQUEST_SCHEMA, decode_spoken_response
from tests.test_meet_dialog_speech_controls import speech_scope
from tests.test_meet_speech_binding import speech_result


@pytest.fixture
def spoken(tmp_path):
    f, scope = speech_scope()
    engine = create_engine(f"sqlite:///{tmp_path / 'spoken.sqlite'}")
    reservations, dispatches = SqlChatReservations(engine), SqlChatDispatches(engine)
    reservations.initialize()
    dispatches.initialize()
    lease = {"sessionId": "ms_" + "a" * 32, "generation": 2, "expiresAt": (f.now + 60) * 1000}
    receipt = {
        "lease": lease,
        "membershipEpoch": 3,
        "receiveRevision": 4,
        "peerId": "b" * 16,
        "grants": [{"chatRead": True, "publisherPeerId": "c" * 16, "expiresAt": (f.now + 60) * 1000}],
    }
    meet, worker, tasks = Mock(), Mock(), Mock()
    meet.inspect.side_effect = lambda *args: copy.deepcopy(receipt)
    tasks.finish.return_value = True

    def generate(turn):
        return speech_result(profile=turn["speech_profile"]) | {
            "task_id": turn["task_id"],
            "lease_id": turn["lease_id"],
            "usage": {"input_tokens": 10, "output_tokens": 8},
        }

    worker.execute.side_effect = generate
    replies = MeetDialogReplies(
        f.binding, worker, tasks, dispatches, clock=lambda: f.now, speech_profile=speech_profile()
    )
    service = MeetDialogSpokenReply(f.authority, meet, reservations, replies, clock=lambda: f.now)
    payload = {
        "schema": REQUEST_SCHEMA,
        "task_id": scope.task_id,
        "lease_id": scope.lease_id,
        "runtime_id": scope.runtime_id,
        "nonce": "d" * 32,
        "sent_at": f.now,
        "meet_session_id": lease["sessionId"],
        "event": {
            "schema": "ananta.meet-chat-event.draft1",
            "session_id": scope.session_id,
            "generation": 2,
            "room_id": scope.room_id,
            "membership_epoch": 3,
            "message_id": "e" * 32,
            "sender_peer_id": "c" * 16,
            "sender_kind": "human",
            "sent_at_ms": f.now * 1000,
            "text": "@ananta Hallo",
        },
    }
    yield f, service, payload, receipt, worker, tasks, generate
    engine.dispose()


def test_spoken_result_is_bound_to_one_real_sql_dispatch_and_contains_no_video_or_grant(spoken):
    f, service, payload, _, worker, tasks, _ = spoken
    response = service.execute(payload)
    reply = response["reply"]
    decoded = decode_spoken_response(response, payload, reply["binding"], f.now * 1000)
    assert len(decoded.pcm) == 882
    assert reply["binding"]["receive_revision"] == 4
    assert reply["binding"]["chat_revision"] == reply["binding"]["speech_revision"] == 1
    assert reply["child_task_id"] == tasks.start.call_args.args[0]["task_id"]
    assert reply["child_lease_id"] == tasks.start.call_args.args[0]["lease_id"]
    assert "video" not in reply and "meeting" not in reply
    assert "meeting" not in worker.execute.call_args.args[0]
    assert service.execute(payload)["reply"] is None
    worker.execute.assert_called_once()


@pytest.mark.parametrize("change", ["speech", "chat", "grant", "input", "machine", "generation", "task", "scope"])
def test_missing_current_authority_never_dispatches_media(spoken, change):
    f, service, payload, receipt, worker, tasks, _ = spoken
    if change == "speech":
        f.context["controls"]["speech"]["enabled"] = False
    elif change == "chat":
        f.context["controls"]["chat"]["enabled"] = False
    elif change == "grant":
        receipt["grants"].clear()
    elif change == "input":
        f.context["controls"]["speech"]["since"] += 1
    elif change == "machine":
        payload["event"]["sender_kind"] = "machine"
    elif change == "generation":
        payload["event"]["generation"] = 1
    elif change == "task":
        f.task.status = "cancelled"
    else:
        payload["runtime_id"] = "other"
    try:
        assert service.execute(payload)["reply"] is None
    except MeetError:
        assert change in ("task", "scope")
    worker.execute.assert_not_called()
    tasks.start.assert_not_called()


@pytest.mark.parametrize("change", ["speech", "chat", "grant", "generation", "membership", "profile", "task_id"])
def test_change_during_generation_discards_output_and_does_not_retry(spoken, change):
    f, service, payload, receipt, worker, tasks, generate = spoken

    def mutate(turn):
        result = generate(turn)
        if change in ("speech", "chat"):
            f.context["controls"][change].update(enabled=False, revision=2)
            f.context["controls"]["revision"] = 2
        elif change == "grant":
            receipt["grants"].clear()
        elif change == "generation":
            receipt["lease"]["generation"] = 3
        elif change == "membership":
            receipt["membershipEpoch"] = 4
        elif change == "profile":
            result["speech"]["profile"]["voice_id"] = "wrong"
        else:
            result["task_id"] = "foreign"
        return result

    worker.execute.side_effect = mutate
    with pytest.raises(MeetError):
        service.execute(payload)
    assert tasks.finish.call_args.args[1] == "failed"
    try:
        assert service.execute(payload)["reply"] is None
    except MeetError:
        pass
    worker.execute.assert_called_once()


def test_source_change_after_result_validation_still_prevents_release(spoken):
    f, service, payload, _, worker, _, _ = spoken
    original = service.replies.execute

    def revoke(*args):
        result = original(*args)
        f.context["controls"]["speech"]["enabled"] = False
        return result

    service.replies.execute = revoke
    with pytest.raises(MeetError, match="authority_changed"):
        service.execute(payload)
    worker.execute.assert_called_once()


def test_malformed_or_stale_input_has_a_bounded_error_before_dispatch(spoken):
    _, service, payload, _, worker, tasks, _ = spoken
    for invalid in (
        payload | {"extra": True},
        payload | {"sent_at": payload["sent_at"] - 11},
        payload | {"event": payload["event"] | {"text": "\ud800"}},
    ):
        with pytest.raises(MeetError) as error:
            service.execute(invalid)
        assert error.value.status == 400
    worker.execute.assert_not_called()
    tasks.start.assert_not_called()
