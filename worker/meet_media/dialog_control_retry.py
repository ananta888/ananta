"""One bounded read-recovery burst, separate from media or authorization state."""

import math


class ControlReadRetry:
    def __init__(self):
        self.reset()

    def reset(self):
        self.deadline = None
        self.not_before = 0.0
        self.attempts = 0
        self.exhausted = False

    def require_window(self, now):
        if not math.isfinite(now) or self.exhausted or self.deadline is not None and now >= self.deadline:
            self.exhausted = True
            raise ValueError("meet_dialog_control_recovery_exhausted")

    def reserve(self, *, now, started, fresh_until):
        if self.deadline is None:
            if (
                not math.isfinite(started)
                or now < started
                or fresh_until is not None
                and not math.isfinite(fresh_until)
            ):
                self.exhausted = True
            self.deadline = min(started + 2.5, fresh_until) if fresh_until is not None else started + 2.5
        self.require_window(now)
        if self.attempts >= 2:
            self.exhausted = True
            raise ValueError("meet_dialog_control_recovery_exhausted")
        delay = (0.1, 0.2)[self.attempts]
        if now + delay >= self.deadline:
            self.exhausted = True
            raise ValueError("meet_dialog_control_recovery_exhausted")
        self.attempts += 1
        self.not_before = now + delay
