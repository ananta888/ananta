"""One disposable dialog session; bounded operations, no retries or authority issuance."""

import math
import time
from collections.abc import Callable
from typing import Protocol

from worker.meet_media.browser_session_phase import START, STATE

_READY = """() => Boolean(window.anantaMachine
  && ['join', 'renew', 'leave'].every(name => typeof window.anantaMachine[name] === 'function'))"""


class DialogSessionPage(Protocol):
    @property
    def url(self) -> str: ...
    def evaluate(self, expression, arg=None): ...
    def wait_for_timeout(self, timeout): ...


class DialogSessionOperations:
    def __init__(self, page: DialogSessionPage, *, url: str, deadline: float, clock=time.monotonic):
        if type(deadline) not in {int, float} or not math.isfinite(deadline):
            raise ValueError("meet_dialog_session_deadline_invalid")
        self.page, self.url, self.clock = page, url, clock
        self.deadline = min(deadline, clock() + 7200)
        self.joined = False
        self.closed = False

    def _check(self, deadline, require_current):
        if self.closed or self.clock() >= deadline:
            raise ValueError("meet_dialog_session_expired")
        if self.page.url != self.url:
            raise ValueError("meet_machine_navigation_denied")
        if require_current is not None:
            require_current()
            # Check again after the injected checkpoint; it cannot extend time
            # or change the page to which an operation is handed off.
            if self.closed or self.clock() >= deadline:
                raise ValueError("meet_dialog_session_expired")
            if self.page.url != self.url:
                raise ValueError("meet_machine_navigation_denied")

    def _wait(self, deadline):
        remaining = deadline - self.clock()
        if remaining <= 0:
            raise ValueError("meet_dialog_session_expired")
        self.page.wait_for_timeout(min(100, remaining * 1000))

    def ready(self):
        deadline = min(self.deadline, self.clock() + 20)
        try:
            if self.joined:
                raise ValueError("meet_dialog_session_transition_invalid")
            while True:
                self._check(deadline, None)
                value = self.page.evaluate(_READY)
                self._check(deadline, None)
                if value is True:
                    return
                self._wait(deadline)
        except Exception:
            self.close()
            raise

    def _call(self, operation, args, budget, require_current=None):
        # Leave creates no authority and retains its own bounded cleanup budget
        # after the assignment expires, like the Hub's terminal finish callback.
        deadline = self.clock() + budget
        if operation != "leave":
            deadline = min(self.deadline, deadline)
        try:
            if self.joined != (operation != "join"):
                raise ValueError("meet_dialog_session_transition_invalid")
            self._check(deadline, require_current)
            self.page.evaluate(START, [operation, args, self.url])
            while True:
                self._check(deadline, require_current)
                state = self.page.evaluate(STATE)
                self._check(deadline, require_current)
                if state == "done":
                    self.joined = operation != "leave"
                    self.closed = operation == "leave"
                    return
                if state != "pending":
                    raise ValueError("meet_dialog_session_operation_failed")
                self._wait(deadline)
        except Exception:
            self.close()
            raise

    def join(self, room: str, grant: str):
        self._call("join", [room, grant], 20)

    def renew(self, grant: str, require_current: Callable[[], None]):
        if not callable(require_current):
            self.close()
            raise ValueError("meet_dialog_session_checkpoint_required")
        self._call("renew", [grant], 2.5, require_current)

    def leave(self):
        self._call("leave", [], 3)

    def close(self):
        self.closed = True  # Caller closes the owned disposable browser; never retry a late operation.
