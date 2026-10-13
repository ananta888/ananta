"""Owner-thread chat/control progress while local execution is waiting."""

import time

from worker.meet_media.companion_chat_ledger import chat_error_reason, port_closed


class CompanionSessionEnded(BaseException):
    """Abort the current conversation, including any in-flight local process.

    This deliberately escapes per-message Exception handlers. Revocation must
    end execution, not become a logged model failure followed by more inputs.
    """


class CompanionChatPump:
    def __init__(self, inbox, *, status, open_chat, poll, redeliver, renew, stopped, log,
                 reply_pending=lambda _identity: True, clock=time.monotonic, renew_interval=40):
        if type(renew_interval) not in (int, float) or not 0 < renew_interval <= 120:
            raise ValueError("meet_companion_renew_interval_invalid")
        self.inbox = inbox
        self.status, self.open_chat, self.poll, self.renew = status, open_chat, poll, renew
        self.stopped, self.log, self.clock = stopped, log, clock
        self.renew_interval = renew_interval
        self.redeliver, self.reply_pending = redeliver, reply_pending
        self.last_redelivery = None
        self.last_renew = clock()
        self.next_renew_attempt = 0
        self.opened = False

    def step(self):
        try:
            if self.stopped():
                raise CompanionSessionEnded("meet_companion_stopped")
            state = self.status()
            if not isinstance(state, dict) or state.get("joined") is not True:
                raise CompanionSessionEnded("meet_companion_session_lost")
            if self.clock() - self.last_renew >= self.renew_interval and self.clock() >= self.next_renew_attempt:
                if self.renew() is False:
                    # Deferral neither extends a lease nor claims renewal.
                    self.next_renew_attempt = self.clock() + 1
                else:
                    self.last_renew = self.clock()
                    self.next_renew_attempt = 0
            if not self.opened:
                self.inbox.bind(self.open_chat())
                self.opened = True
            # Finite pages, no ACK. Every poll revalidates browser authority.
            for _ in range(4):
                previous_cursor = self.inbox.cursor
                try:
                    batch = self.poll(self.inbox.cursor)
                except Exception as error:  # noqa: BLE001
                    if not port_closed(error):
                        raise
                    self.inbox.bind(self.open_chat())
                    self.opened = True
                    previous_cursor = 0
                    batch = self.poll(0)
                self.inbox.accept(batch)
                if not batch["events"]:
                    break
                if self.inbox.cursor <= previous_cursor:
                    raise ValueError("meet_chat_pagination_unsupported")
            # Already delivered inputs may wait behind several model rounds.
            # Only the browser can reconfirm current source/read authority.
            now = self.clock()
            if self.last_redelivery is None or now - self.last_redelivery >= 10:
                ids = [identity for identity in self.inbox.pending_message_ids() if self.reply_pending(identity)]
                if ids:
                    receipt = self.redeliver(ids)
                    if (not isinstance(receipt, dict) or set(receipt) != {"schema", "message_ids"}
                            or receipt["schema"] != "ananta.meet-chat-redelivery.draft1"
                            or receipt["message_ids"] != ids):
                        raise ValueError("meet_chat_redelivery_receipt_invalid")
                self.last_redelivery = now
            return state
        except CompanionSessionEnded:
            self.inbox.clear()
            raise
        except Exception as error:  # noqa: BLE001
            self.inbox.clear()
            self.log("chat_control_err reason=%s" % chat_error_reason(error))
            raise CompanionSessionEnded("meet_companion_control_lost") from None
