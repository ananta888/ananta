"""Lease-polled operations in one disposable Meet page; no retries or scheduling."""

import math
import time
from typing import Protocol


class PublicationPage(Protocol):
    @property
    def url(self) -> str: ...

    def evaluate(self, expression, arg=None): ...

    def wait_for_timeout(self, timeout): ...


_START = """([operation, args, expectedUrl]) => {
  if (window.location.href !== expectedUrl) throw new Error('meet_machine_navigation_denied');
  const phase = { operation, status: 'pending' };
  window.__anantaPublicationPhase = phase;
  void Promise.resolve().then(() => window.anantaMachine[operation](...args)).then(
    () => { if (window.__anantaPublicationPhase === phase) phase.status = 'done'; },
    () => { if (window.__anantaPublicationPhase === phase) phase.status = 'failed'; });
}"""
_STATE = "() => window.__anantaPublicationPhase?.status"
_READY = """() => Boolean(window.anantaMachine
  && ['join', 'publish', 'leave'].every(name => typeof window.anantaMachine[name] === 'function'))"""


class PublicationSession:
    def __init__(self, page: PublicationPage, lease, *, url, deadline, clock=time.monotonic, wall=time.time):
        if type(deadline) not in {int, float} or not math.isfinite(deadline):
            raise ValueError("meet_publication_deadline_invalid")
        self.page, self.lease, self.url, self.clock = page, lease, url, clock
        # Wall-clock changes cannot extend the original Hub assignment.
        self.deadline = clock() + min(120, max(0, deadline - wall()))

    def _require(self, deadline):
        if self.clock() >= deadline:
            raise ValueError("meet_publication_expired")
        if self.page.url != self.url:
            raise ValueError("meet_machine_navigation_denied")
        self.lease.require()
        if self.clock() >= deadline:
            raise ValueError("meet_publication_expired")
        if self.page.url != self.url:
            raise ValueError("meet_machine_navigation_denied")

    def ready(self):
        deadline = min(self.deadline, self.clock() + 20)
        while True:
            self._require(deadline)
            ready = self.page.evaluate(_READY)
            self._require(deadline)
            if ready is True:
                return
            self._wait(deadline)

    def call(self, operation, args):
        budgets = {"join": 20, "publish": 85, "leave": 3}
        if operation not in budgets:
            raise ValueError("meet_publication_operation_invalid")
        deadline = min(self.deadline, self.clock() + budgets[operation])
        self._require(deadline)
        # Never return/await the remote operation's Promise in Python's RPC:
        # an unresolved join/leave must not suspend lease polling.
        self.page.evaluate(_START, [operation, args, self.url])
        while True:
            self._require(deadline)
            state = self.page.evaluate(_STATE)
            self._require(deadline)
            if state == "done":
                return
            if state != "pending":
                raise ValueError("meet_publication_operation_failed")
            self._wait(deadline)

    def _wait(self, deadline):
        remaining = deadline - self.clock()
        if remaining <= 0:
            raise ValueError("meet_publication_expired")
        self.page.wait_for_timeout(min(250, remaining * 1000))
