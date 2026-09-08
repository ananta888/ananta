"""Pure resource-stop budget; progress is not Hub authorization or a new lease."""

import math


def instant(value):
    return type(value) in {int, float} and math.isfinite(value) and value > 0


class DialogProgressBudget:
    def __init__(self, hard_deadline, now):
        if not instant(now) or not instant(hard_deadline) or not now < hard_deadline <= now + 7205:
            raise ValueError("meet_dialog_progress_deadline_invalid")
        self.hard_deadline = hard_deadline
        self.deadline = min(hard_deadline, now + 90)
        self.last_progress = None
        self.last_now = now
        self.closed = False

    def _fail(self, code):
        self.closed = True
        raise ValueError(code)

    def require(self, now):
        if self.closed or not instant(now) or now < self.last_now or now >= self.deadline:
            self._fail("meet_dialog_progress_expired")
        self.last_now = now

    def observe(self, fresh_until, now):
        self.require(now)  # An expired parent cannot be revived by a queued packet.
        if (
            not instant(fresh_until)
            or not now < fresh_until <= now + 2.5
            or self.last_progress is not None
            and fresh_until < self.last_progress
        ):
            self._fail("meet_dialog_progress_invalid")
        self.last_progress = fresh_until
        self.deadline = min(self.hard_deadline, fresh_until + 0.5)
