"""Process one retained page; ACK only its contiguous finished prefix."""

from worker.meet_media.companion_chat_ledger import chat_error_reason, serve_batch


def serve_inbox(inbox, ledger, *, send, ack, answer, log):
    # Freeze this page before model/TTS work. Concurrent cooperative polling
    # may add later pages; their cursors must never join this ACK.
    batch = inbox.batch()
    def bounded_answer(event, deliver):
        try:
            answer(event, deliver)
        except Exception:  # noqa: BLE001
            # Ordinary execution failure must not silently ACK an unanswered
            # input. Control loss is BaseException and never sends here.
            if event["message_id"] not in ledger.answered and ledger.pending_reply(event["message_id"]) is None:
                deliver("Die Antwort konnte nicht erstellt werden.")
            raise

    serve_batch(batch, ledger, send=send, ack=lambda _cursor: None, answer=bounded_answer, log=log)
    finished = []
    for item in batch["events"]:
        identity = item["event"]["message_id"]
        if ledger.pending_reply(identity) is not None:
            # A fenced send needs this delivery to remain retryable. A
            # reopened port must not silently ACK it before the resend.
            break
        finished.append(identity)
    cursor = inbox.completed(finished)
    if cursor is not None:
        try:
            ack(cursor)
        except Exception as error:  # noqa: BLE001
            log("chat_ack_err reason=%s" % chat_error_reason(error))
