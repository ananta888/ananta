"""Bound browser progress cost; cached status never authorizes publication."""

import time

BROWSER_PROGRESS_INTERVAL_SECONDS = .1


class CompanionBrowserProgress:
    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self.next_poll = 0
        self.state = None

    def step(self, *, poll, pulse):
        now = self.clock()
        if self.state is None or now >= self.next_poll:
            self.state = None
            pulse()
            self.state = poll()
            self.next_poll = now + BROWSER_PROGRESS_INTERVAL_SECONDS
        return self.state
