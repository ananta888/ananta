"""One negotiated reconnect window; no timer, scheduler or grant issuance."""

import time

from ananta_contracts.meet_reconnect import validate_reconnect_response


class ReconnectReceiptGate:
    def __init__(self, assignment, *, clock=time.time):
        if "reconnect" in assignment and assignment["reconnect"] is not True:
            raise ValueError("meet_reconnect_negotiation_invalid")
        self.enabled = assignment.get("reconnect") is True
        self.binding = (
            {
                "reconnect": True,
                "deadline": assignment["deadline"],
                "meeting": {key: assignment["meeting"][key] for key in ("origin", "room_id")},
            }
            if self.enabled
            else None
        )
        self.clock = clock
        self.attempt, self.window, self.phase, self.grant_seen, self.closed = 0, None, None, False, False

    def require_enabled(self):
        if not self.enabled or self.closed:
            raise ValueError("meet_reconnect_not_available")

    def accept(self, value, request):
        self.require_enabled()
        try:
            accepted = validate_reconnect_response(value, request, self.binding, int(self.clock() * 1000), self.attempt)
            window = accepted["deadline_ms"], accepted["ready_ms"]
            starting = request["attempt"] == 0
            if not starting and (window != self.window or self.phase == "joining" and accepted["state"] == "waiting"):
                raise ValueError("meet_reconnect_window_changed")
            if accepted["meeting"] is not None and not starting and self.grant_seen:
                raise ValueError("meet_reconnect_grant_replayed")
            self.attempt, self.window, self.phase = accepted["attempt"], window, accepted["state"]
            self.grant_seen = accepted["meeting"] is not None or (not starting and self.grant_seen)
            return accepted
        except Exception:
            self.close()
            raise

    def close(self):
        self.closed = True
