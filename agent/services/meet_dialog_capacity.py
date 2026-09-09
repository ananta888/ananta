"""Bounded Hub wait around one already-selected publisher dispatch."""

import time
from typing import Protocol

from agent.models.meet_dialog_capacity import reservation
from agent.services.meet_contract import MeetError


class DialogCapacitySlots(Protocol):
    def reserve(self, binding, now): ...
    def poll(self, identity, binding, now): ...
    def cancel_before_dispatch(self, identity, binding, now): ...


class DialogCapacityAuthority(Protocol):
    def current(self, task_id, lease_id, runtime_id): ...


class MeetDialogCapacity:
    def __init__(
        self,
        slots: DialogCapacitySlots,
        authority: DialogCapacityAuthority,
        *,
        clock=time.time,
        monotonic=time.monotonic,
        wait=time.sleep,
    ):
        self.slots, self.authority = slots, authority
        self.clock, self.monotonic, self.wait = clock, monotonic, wait

    def dispatch(self, scope, publisher, operation):
        binding = reservation(scope, publisher)
        until = self.monotonic() + min(10, max(0, scope.deadline - self.clock()))

        def require_budget():
            if self.clock() >= scope.deadline or self.monotonic() >= until:
                raise MeetError("meet_dialog_capacity_wait_expired", 429)

        def require():
            require_budget()
            if self.authority.current(scope.task_id, scope.lease_id, scope.runtime_id) != scope:
                raise MeetError("meet_dialog_capacity_authority_changed", 403)
            require_budget()  # A slow authoritative database read cannot extend admission.

        require()
        identity = self.slots.reserve(binding, self.clock())
        dispatched = False
        try:
            while True:
                require()
                if self.slots.poll(identity, binding, self.clock()):
                    break
                self.wait(min(0.1, max(0, until - self.monotonic())))
            require()
            dispatched = True
            return operation()
        finally:
            # A successful acceptance only means the asynchronous browser was
            # started. Neither success nor an uncertain HTTP return frees it.
            if not dispatched:
                self.slots.cancel_before_dispatch(identity, binding, self.clock())
