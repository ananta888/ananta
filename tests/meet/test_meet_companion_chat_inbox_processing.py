"""Real ledger + inbox interaction during execution and fenced delivery."""

from worker.meet_media.companion_chat_inbox import CompanionChatInbox
from worker.meet_media.companion_chat_inbox_processing import serve_inbox
from worker.meet_media.companion_chat_ledger import ChatLedger


def page(start=1, count=8):
    return {"schema": "ananta.meet-chat-batch.draft1", "events": [
        {"cursor": number, "event": {"message_id": f"{number:032x}", "text": "synthetic"}}
        for number in range(start, start + count)]}


def test_later_poll_during_answer_does_not_advance_current_ack():
    inbox, ledger, acknowledgements, answers = CompanionChatInbox(), ChatLedger(), [], []
    inbox.accept(page())

    def answer(event, deliver):
        if not answers:
            inbox.accept(page(9))
        answers.append(event["message_id"])
        deliver("synthetic answer")

    serve_inbox(inbox, ledger, send=lambda *args: None, ack=acknowledgements.append,
                answer=answer, log=lambda _message: None)
    assert len(answers) == 8 and acknowledgements == [8]
    assert inbox.batch()["events"][0]["cursor"] == 9


def test_fenced_reply_remains_pending_until_successful_resend_without_second_answer():
    inbox, ledger, acknowledgements, answers = CompanionChatInbox(), ChatLedger(), [], []
    inbox.accept(page(1, 1))

    def send(*args):
        raise RuntimeError("Page.evaluate: Error: meet_chat_closed")

    def answer(event, deliver):
        answers.append(event["message_id"])
        deliver("synthetic answer")

    serve_inbox(inbox, ledger, send=send, ack=acknowledgements.append, answer=answer, log=lambda _message: None)
    assert acknowledgements == [] and len(inbox.batch()["events"]) == 1
    sent = []
    serve_inbox(inbox, ledger, send=lambda *args: sent.append(args), ack=acknowledgements.append,
                answer=answer, log=lambda _message: None)
    assert len(answers) == 1 and sent == [(f"{1:032x}", "synthetic answer")]
    assert acknowledgements == [1] and not inbox.batch()["events"]


def test_execution_failure_sends_bounded_notice_instead_of_silently_acknowledging():
    inbox, ledger, acknowledgements, sent, logs = CompanionChatInbox(), ChatLedger(), [], [], []
    inbox.accept(page(1, 1))

    def answer(*args):
        raise RuntimeError("synthetic-private-provider-error")

    serve_inbox(inbox, ledger, send=lambda *args: sent.append(args), ack=acknowledgements.append,
                answer=answer, log=logs.append)
    assert sent == [(f"{1:032x}", "Die Antwort konnte nicht erstellt werden.")]
    assert acknowledgements == [1] and not inbox.batch()["events"]
    assert all("synthetic-private-provider-error" not in value for value in logs)
