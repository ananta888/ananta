"""At-most-once companion answers across chat-port reopenings.

The browser chat port is fenced on every lease renewal and reopened by the
companion loop. The client carries unacknowledged inputs into the reopened
port; this ledger keeps the companion side idempotent over ``message_id``:

* an input is answered at most once, also when it is re-delivered after a
  reopen or when the bounded memory of handled IDs had to evict entries,
  because answers already visible in the client's chat state count as answered;
* a reply whose send met a closed port is kept (bounded) and re-sent without a
  second model/TTS round once the reopened port re-delivers the same ID;
* human messages that show up in the chat state but are never delivered are
  reported once by ID only. They are not answered: the client only accepts a
  reply for an ID it delivered itself.

It holds IDs and at most a few pending reply texts, never transcripts, and it
never logs text.
"""

import re
import time
from collections import OrderedDict

MESSAGE_ID = re.compile(r"^[a-f0-9]{32}$")
# Client limits: 512 answered IDs, 32 pending inputs, 30 s input freshness.
ANSWERED_LIMIT = 512
UNSENT_LIMIT = 32
GAP_GRACE_SECONDS = 8.0
GAP_FORGET_SECONDS = 120.0
PORT_CLOSED = ("meet_chat_closed", "meet_chat_authority_changed")

NEW = "new"
RETRY = "retry"


def port_closed(error):
    """True when a chat call failed only because the port was fenced/closed."""
    message = str(error)
    return any(code in message for code in PORT_CLOSED)


class _BoundedSet:
    def __init__(self, limit):
        self.limit, self.items = limit, OrderedDict()

    def add(self, key, value=True):
        self.items[key] = value
        self.items.move_to_end(key)
        while len(self.items) > self.limit:
            self.items.popitem(last=False)

    def __contains__(self, key):
        return key in self.items

    def pop(self, key):
        return self.items.pop(key, None)

    def get(self, key):
        return self.items.get(key)


class ChatLedger:
    def __init__(self, *, clock=time.time, grace_seconds=GAP_GRACE_SECONDS):
        self.clock, self.grace = clock, grace_seconds
        self.answered = _BoundedSet(ANSWERED_LIMIT)
        self.handled = _BoundedSet(ANSWERED_LIMIT)
        self.unsent = _BoundedSet(UNSENT_LIMIT)
        self.undelivered = OrderedDict()  # message_id -> first seen in chat state
        self.reported = _BoundedSet(ANSWERED_LIMIT)

    def observe(self, chat):
        """Reconcile with the client's chat state (``status().chat``)."""
        now = self.clock()
        humans = []
        for entry in chat if isinstance(chat, list) else ():
            if not isinstance(entry, dict) or entry.get("system"):
                continue
            reply_to = entry.get("replyTo")
            if entry.get("machine"):
                if isinstance(reply_to, str) and MESSAGE_ID.match(reply_to):
                    self.answered.add(reply_to)
                    self.unsent.pop(reply_to)
                continue
            message_id = entry.get("messageId")
            if isinstance(message_id, str) and MESSAGE_ID.match(message_id) and not reply_to:
                humans.append(message_id)
        for message_id in humans:
            if message_id not in self.answered and message_id not in self.handled:
                self.undelivered.setdefault(message_id, now)
        for message_id, seen in list(self.undelivered.items()):
            if message_id in self.answered or message_id in self.handled or now - seen > GAP_FORGET_SECONDS:
                del self.undelivered[message_id]

    def admit(self, event):
        """Classify a delivered event: NEW (answer it), RETRY (re-send cached reply) or None."""
        if not isinstance(event, dict) or event.get("sender_kind", "human") != "human":
            return None
        message_id = event.get("message_id")
        if not isinstance(message_id, str) or not MESSAGE_ID.match(message_id):
            return None
        if not str(event.get("text") or "").strip() or message_id in self.answered:
            return None
        self.undelivered.pop(message_id, None)
        if self.unsent.get(message_id) is not None:
            return RETRY
        if message_id in self.handled:
            return None
        self.handled.add(message_id)
        return NEW

    def pending_reply(self, message_id):
        return self.unsent.get(message_id)

    def replied(self, message_id):
        self.answered.add(message_id)
        self.unsent.pop(message_id)

    def reply_failed(self, message_id, reply, error):
        """Keep the reply for a re-delivery only if the port was merely fenced."""
        if port_closed(error) and isinstance(reply, str) and reply.strip():
            self.unsent.add(message_id, reply)
        else:
            # Denied or uncertain delivery: never try a second answer.
            self.answered.add(message_id)
            self.unsent.pop(message_id)

    def gaps(self):
        """IDs seen in the chat state but never delivered after the grace period; reported once."""
        now, due = self.clock(), []
        for message_id, seen in self.undelivered.items():
            if now - seen >= self.grace and message_id not in self.reported:
                self.reported.add(message_id)
                due.append(message_id)
        return due


def serve_batch(batch, ledger, *, send, ack, answer, log):
    """Handle one ``chat.poll()`` batch at most once per ``message_id``.

    ``send(message_id, text)`` is the client's ``chat.reply``; ``answer(event,
    deliver)`` runs the model/TTS round and calls ``deliver(reply)`` once to
    post the chat reply. ``ack(cursor)`` runs after all inputs were handled.
    """

    def deliver(message_id, reply):
        try:
            send(message_id, reply)
        except Exception as error:  # noqa: BLE001
            ledger.reply_failed(message_id, reply, error)
            log("chat_reply_err %r" % (error,))
            return False
        ledger.replied(message_id)
        return True

    for item in (batch or {}).get("events") or ():
        event = item.get("event") if isinstance(item, dict) else None
        verdict = ledger.admit(event)
        if verdict == RETRY:
            # Answered and spoken before a fence closed the port: post the
            # same reply once, without a second model/TTS round.
            message_id = event["message_id"]
            if deliver(message_id, ledger.pending_reply(message_id)):
                log("chat_reply_resent id=%s" % message_id[:8])
        elif verdict == NEW:
            message_id = event["message_id"]
            try:
                answer(event, lambda reply, message_id=message_id: deliver(message_id, reply))
            except Exception as error:  # noqa: BLE001
                log("reply_err %r" % (error,))
    cursor = ack_cursor(batch)
    if cursor is not None:
        try:
            ack(cursor)
        except Exception as error:  # noqa: BLE001
            log("chat_ack_err %r" % (error,))


def ack_cursor(batch):
    """Highest delivered cursor of a poll batch, or None when nothing was delivered."""
    cursors = [
        item.get("cursor")
        for item in (batch or {}).get("events") or ()
        if isinstance(item, dict) and type(item.get("cursor")) is int and item.get("cursor") > 0
    ]
    return max(cursors) if cursors else None
