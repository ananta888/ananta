"""Companion chat delivery across chat-port reopenings (lease-renewal fences)."""

import pytest

from worker.meet_media.companion_chat_ledger import (
    ANSWERED_LIMIT,
    NEW,
    RETRY,
    ChatLedger,
    ack_cursor,
    port_closed,
    serve_batch,
)

pytestmark = pytest.mark.timeout(30)


def mid(char):
    return char * 32


class FakeClientChat:
    """Mirrors the client port: fenced on renewal, carries unacknowledged inputs
    into the next open(), replies only to delivered IDs and at most once."""

    def __init__(self):
        self.open_, self.cursor = True, 0
        self.pending, self.delivered, self.replied, self.chat = [], set(), set(), []

    def human(self, message_id, text="Frage"):
        self.chat.append({"id": len(self.chat) + 1, "author": "Mensch", "text": text, "system": False,
                          "messageId": message_id, "replyTo": ""})
        self.pending.append({"cursor": 0, "event": {"message_id": message_id, "sender_peer_id": "1" * 16,
                                                     "sender_kind": "human", "text": text}})

    def fence(self):
        self.open_ = False

    def reopen(self):
        self.open_ = True

    def _require_open(self):
        if not self.open_:
            raise RuntimeError("Error: meet_chat_closed")

    def poll(self):
        self._require_open()
        for item in self.pending:
            if not item["cursor"]:
                self.cursor += 1
                item["cursor"] = self.cursor
        batch = [dict(item) for item in self.pending[:8]]
        self.delivered.update(item["event"]["message_id"] for item in batch)
        return {"schema": "ananta.meet-chat-batch.draft1", "acknowledged": 0, "events": batch}

    def ack(self, cursor):
        self._require_open()
        self.pending = [item for item in self.pending if item["cursor"] > cursor]

    def reply(self, message_id, text):
        self._require_open()
        if message_id not in self.delivered or message_id in self.replied:
            raise RuntimeError("Error: meet_chat_reply_denied")
        self.replied.add(message_id)
        self.chat.append({"id": len(self.chat) + 1, "author": "ai-snake", "text": text, "system": False,
                          "machine": True, "messageId": "f" * 32, "replyTo": message_id})


class Companion:
    """The companion loop's chat part, with model/TTS mocked."""

    def __init__(self, client, *, clock=None):
        self.client, self.logs, self.answered, self.spoken = client, [], [], []
        self.ledger = ChatLedger(clock=clock) if clock else ChatLedger()
        self.reply_hook = None

    def answer(self, event, deliver):
        self.answered.append(event["message_id"])
        reply = "Antwort auf %s" % event["message_id"][:4]
        if self.reply_hook:
            self.reply_hook()
        deliver(reply)
        self.spoken.append(reply)

    def tick(self):
        self.ledger.observe(self.client.chat)
        for missing in self.ledger.gaps():
            self.logs.append("chat_gap id=%s undelivered" % missing[:8])
        try:
            batch = self.client.poll()
        except RuntimeError as error:
            self.logs.append("chat_poll_err %r" % (error,))
            assert port_closed(error)
            self.client.reopen()
            return
        serve_batch(batch, self.ledger, send=self.client.reply, ack=self.client.ack,
                    answer=self.answer, log=self.logs.append)


def test_a_message_arriving_in_the_renewal_gap_is_answered_once_after_the_reopen():
    client = FakeClientChat()
    companion = Companion(client)
    companion.tick()
    client.fence()
    client.human(mid("a"), "rag-helper erklaeren")
    companion.tick()  # poll fails on the fenced port and reopens it
    companion.tick()
    companion.tick()
    assert companion.answered == [mid("a")]
    assert client.replied == {mid("a")}
    assert client.pending == []  # ACKed with the highest delivered cursor


def test_a_reply_that_met_a_fence_is_resent_once_without_a_second_model_round():
    client = FakeClientChat()
    companion = Companion(client)
    client.human(mid("b"))
    companion.reply_hook = client.fence  # renewal lands while the model is answering
    companion.tick()
    companion.reply_hook = None
    assert client.replied == set()
    assert companion.spoken == ["Antwort auf bbbb"]
    companion.tick()  # closed -> reopen
    companion.tick()  # the client re-delivers the unacknowledged input
    companion.tick()
    assert companion.answered == [mid("b")]
    assert client.replied == {mid("b")}
    assert len(companion.spoken) == 1
    assert any(line.startswith("chat_reply_resent id=bbbbbbbb") for line in companion.logs)


def test_a_denied_reply_is_never_retried_or_answered_again():
    client = FakeClientChat()
    companion = Companion(client)
    client.human(mid("c"))
    client.replied.add(mid("c"))  # e.g. reserved by an uncertain earlier send
    companion.tick()
    client.pending.append({"cursor": 0, "event": {"message_id": mid("c"), "sender_kind": "human", "text": "x"}})
    companion.tick()
    assert companion.answered == [mid("c")]
    assert companion.ledger.admit({"message_id": mid("c"), "sender_kind": "human", "text": "x"}) is None


def test_answers_visible_in_the_chat_state_are_never_repeated_by_a_fresh_ledger():
    client = FakeClientChat()
    first = Companion(client)
    client.human(mid("d"))
    first.tick()
    # A restarted companion (empty memory) sees the same input re-delivered.
    client.pending.append({"cursor": 0, "event": {"message_id": mid("d"), "sender_kind": "human", "text": "Frage"}})
    second = Companion(client)
    second.tick()
    assert first.answered == [mid("d")]
    assert second.answered == []


def test_undelivered_messages_in_the_chat_state_are_reported_once_by_id_only():
    now = [1000.0]
    client = FakeClientChat()
    companion = Companion(client, clock=lambda: now[0])
    client.human(mid("e"), "geheimer Inhalt")
    client.pending.clear()  # lost before the client fix: never reaches a subscriber
    companion.tick()
    now[0] += 9
    companion.tick()
    companion.tick()
    gaps = [line for line in companion.logs if line.startswith("chat_gap")]
    assert gaps == ["chat_gap id=eeeeeeee undelivered"]
    assert not any("geheim" in line for line in companion.logs)
    assert companion.answered == []


def test_admission_rules_and_bounds():
    ledger = ChatLedger()
    assert ledger.admit({"message_id": mid("1"), "sender_kind": "machine", "text": "x"}) is None
    assert ledger.admit({"message_id": "not-an-id", "sender_kind": "human", "text": "x"}) is None
    assert ledger.admit({"message_id": mid("2"), "sender_kind": "human", "text": "  "}) is None
    assert ledger.admit({"message_id": mid("3"), "sender_kind": "human", "text": "x"}) == NEW
    assert ledger.admit({"message_id": mid("3"), "sender_kind": "human", "text": "x"}) is None
    ledger.reply_failed(mid("3"), "Antwort", RuntimeError("Error: meet_chat_authority_changed"))
    assert ledger.admit({"message_id": mid("3"), "sender_kind": "human", "text": "x"}) == RETRY
    ledger.replied(mid("3"))
    assert ledger.admit({"message_id": mid("3"), "sender_kind": "human", "text": "x"}) is None
    for number in range(ANSWERED_LIMIT + 100):
        ledger.admit({"message_id": "%032x" % (number + 1000), "sender_kind": "human", "text": "x"})
    assert len(ledger.handled.items) == ANSWERED_LIMIT


def test_ack_cursor_uses_the_highest_delivered_cursor():
    assert ack_cursor({"events": [{"cursor": 3}, {"cursor": 5}, {"cursor": 4}]}) == 5
    assert ack_cursor({"events": []}) is None
    assert ack_cursor({"cursor": 7}) is None
    assert ack_cursor(None) is None
    assert ack_cursor({"events": [{"cursor": True}, {"cursor": "9"}]}) is None
