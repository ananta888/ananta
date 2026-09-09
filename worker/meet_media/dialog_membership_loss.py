"""Recognize loss of confirmed membership; never turn setup failure into retry."""

import time
from contextlib import contextmanager

LOCAL_MEMBERSHIP = "(({joined, lease}) => ({joined, lease}))(window.anantaMachine.status())"


class DialogMembershipLost(ValueError):
    def __init__(self, session_id):
        super().__init__("meet_dialog_confirmed_membership_lost")
        self.session_id = session_id


class MembershipCheckpoint:
    def __init__(self, *, enabled, page, session, url, deadline, clock=time.monotonic):
        self.enabled, self.page, self.session = enabled is True, page, session
        self.url, self.deadline, self.clock = url, deadline, clock
        self.confirmed = None

    def confirm(self, receipt):
        # Caller has already compared a validated Hub receipt to local membership.
        self.confirmed = receipt["lease"]["sessionId"]

    def _lost(self):
        if not self.enabled or self.confirmed is None or self.session.closed:
            return False
        try:
            if self.clock() >= self.deadline or self.page.url != self.url:
                return False
            local = self.page.evaluate(LOCAL_MEMBERSHIP)
            return (
                self.clock() < self.deadline
                and self.page.url == self.url
                and isinstance(local, dict)
                and local.get("joined") is False
            )
        except Exception:
            return False  # Renderer loss/ambiguous state cannot authorize recovery.

    @contextmanager
    def guard(self):
        try:
            yield
        except Exception:
            if self._lost():
                raise DialogMembershipLost(self.confirmed) from None
            raise
