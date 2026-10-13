"""Owner-thread admission, renewal and fail-closed cancellation controls."""

from unittest.mock import Mock

import pytest

from worker.meet_media.companion_chat_inbox import CompanionChatInbox
from worker.meet_media.companion_chat_pump import CompanionChatPump, CompanionSessionEnded


def receipt(ids):
    return {"schema": "ananta.meet-chat-redelivery.draft1", "message_ids": ids}


def scope(generation=1):
    return {"origin": "https://meet.invalid", "tenant_id": "tenant", "project_id": "project",
            "task_id": "task", "session_id": "session", "runtime_id": "runtime", "lease_id": "lease",
            "room_id": "room-aaaaaaaaaaaaaaaaaa", "own_peer_id": "machine", "generation": generation,
            "membership_epoch": 1, "policy_revision": 1, "deadline_ms": 1_000_000}


def event(number, generation=1):
    return {"cursor": number, "event": {"message_id": f"{number:032x}", "text": "synthetic",
            "sender_peer_id": "human", "session_id": "session", "room_id": "room-aaaaaaaaaaaaaaaaaa",
            "generation": generation, "membership_epoch": 1}}


def test_all_pages_are_admitted_without_ack_and_renewal_rebinds_current_scope():
    now, generation, calls = [0], [1], []

    def poll(cursor):
        calls.append(cursor)
        if generation[0] == 2 and cursor:
            raise RuntimeError("Page.evaluate: Error: meet_chat_authority_changed")
        records = [event(i, generation[0]) for i in range(1, 33) if i > cursor][:8]
        return {"schema": "ananta.meet-chat-batch.draft1", "events": records}

    def renew():
        generation[0] += 1

    inbox = CompanionChatInbox()
    pump = CompanionChatPump(inbox, status=lambda: {"joined": True}, open_chat=lambda: scope(generation[0]),
                             poll=poll, redeliver=receipt, renew=renew, stopped=lambda: False, log=lambda _: None,
                             clock=lambda: now[0])
    pump.step()
    assert calls == [0, 8, 16, 24] and inbox.cursor == 32
    # The renewed port reports its fence once, then accepts current pagination.
    raised = [False]
    original = pump.poll

    def renewed_poll(cursor):
        if not raised[0]:
            raised[0] = True
            return original(cursor)
        return {"schema": "ananta.meet-chat-batch.draft1", "events":
                [event(i, generation[0]) for i in range(1, 33) if i > cursor][:8]}

    pump.poll = renewed_poll
    now[0] = 41
    pump.step()
    assert generation[0] == 2 and inbox.cursor == 32
    assert inbox.batch()["events"][0]["event"]["generation"] == 2
    assert inbox.acknowledgable_cursor == 0


@pytest.mark.parametrize("failure", ["revoked", "foreign", "stopped"])
def test_control_loss_escapes_message_handler_and_clears_all_held_input(failure):
    inbox = CompanionChatInbox()
    inbox.bind(scope())
    inbox.accept({"schema": "ananta.meet-chat-batch.draft1", "events": [event(1)]})

    def poll(_cursor):
        raise RuntimeError("Page.evaluate: Error: meet_chat_receive_denied")

    foreign = {**scope(), "project_id": "foreign"}
    logs = []
    pump = CompanionChatPump(inbox, status=lambda: {"joined": True}, open_chat=lambda: foreign,
                             poll=poll, redeliver=receipt, renew=lambda: None,
                             stopped=lambda: failure == "stopped", log=logs.append)
    if failure == "revoked":
        pump.opened = True
    with pytest.raises(CompanionSessionEnded):
        pump.step()
    assert not inbox.batch()["events"]


def test_legacy_client_ignoring_cursor_cannot_silently_claim_continuous_polling():
    inbox = CompanionChatInbox()
    pump = CompanionChatPump(inbox, status=lambda: {"joined": True}, open_chat=lambda: scope(),
                             poll=lambda _cursor: {"schema": "ananta.meet-chat-batch.draft1", "events": [event(1)]},
                             redeliver=receipt, renew=lambda: None, stopped=lambda: False, log=lambda _: None)
    with pytest.raises(CompanionSessionEnded):
        pump.step()
    assert not inbox.batch()["events"]


def test_redelivery_is_bounded_and_excludes_already_answered_inputs():
    now, answered, calls = [0], set(), []
    inbox = CompanionChatInbox()

    def redeliver(ids):
        calls.append(list(ids))
        return receipt(ids)

    pump = CompanionChatPump(
        inbox, status=lambda: {"joined": True}, open_chat=lambda: scope(),
        poll=lambda cursor: {"schema": "ananta.meet-chat-batch.draft1", "events":
                             [event(i) for i in range(1, 33) if i > cursor][:8]},
        redeliver=redeliver, reply_pending=lambda identity: identity not in answered,
        renew=lambda: None, stopped=lambda: False, log=lambda _: None, clock=lambda: now[0])
    pump.step()
    assert len(calls) == 1 and len(calls[0]) == 32
    answered.add(f"{1:032x}")
    now[0] = 9
    pump.step()
    assert len(calls) == 1
    now[0] = 10
    pump.step()
    assert calls[1] == [f"{i:032x}" for i in range(2, 33)]
    assert inbox.acknowledgable_cursor == 0


def test_deferred_renewal_remains_due_and_retries_without_claiming_success():
    now = [0]
    renew = Mock(side_effect=[False, None])
    pump = CompanionChatPump(CompanionChatInbox(), status=lambda: {"joined": True}, open_chat=lambda: scope(),
        poll=lambda _cursor: {"schema": "ananta.meet-chat-batch.draft1", "events": []},
        redeliver=receipt, renew=renew, stopped=lambda: False, log=lambda _: None, clock=lambda: now[0])
    now[0] = 41
    pump.step()
    assert pump.last_renew == 0 and renew.call_count == 1
    now[0] = 41.5
    pump.step()
    assert renew.call_count == 1
    now[0] = 42
    pump.step()
    assert pump.last_renew == 42 and renew.call_count == 2


@pytest.mark.parametrize("failure", ["denied", "unsupported", "wrong_ids", "extra_fields"])
def test_missing_or_invalid_current_redelivery_aborts_the_conversation(failure):
    inbox = CompanionChatInbox()
    logs = []

    def redeliver(ids):
        if failure == "denied":
            raise RuntimeError("Page.evaluate: Error: meet_chat_redelivery_denied")
        if failure == "unsupported":
            raise RuntimeError("Page.evaluate: TypeError: chat.redeliver is not a function")
        if failure == "wrong_ids":
            return receipt([])
        return {**receipt(ids), "untrusted": "private content"}

    pump = CompanionChatPump(
        inbox, status=lambda: {"joined": True}, open_chat=lambda: scope(),
        poll=lambda cursor: {"schema": "ananta.meet-chat-batch.draft1", "events": [event(1)] if not cursor else []},
        redeliver=redeliver, renew=lambda: None, stopped=lambda: False, log=logs.append)
    with pytest.raises(CompanionSessionEnded):
        pump.step()
    assert inbox.pending_message_ids() == []
    assert logs == ["chat_control_err reason=chat_operation_failed"]
