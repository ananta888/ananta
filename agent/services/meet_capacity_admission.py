"""Bounded request-side wait for a normal Hub task's private media capacity."""

import time
from typing import Protocol

from agent.services.meet_contract import MeetError


class MediaCapacityPort(Protocol):
    def run(self, turn, operation, require_current): ...


class CapacitySlotsPort(Protocol):
    def reserve(self, turn, now): ...
    def poll(self, identity, turn, now): ...
    def finish(self, identity, turn, now, *, uncertain=False): ...


class TaskAuthorityPort(Protocol):
    def require_current(self, turn): ...


class MeetCapacityAdmission:
    def __init__(
        self,
        slots: CapacitySlotsPort,
        tasks: TaskAuthorityPort,
        *,
        clock=time.time,
        monotonic=time.monotonic,
        wait=time.sleep,
    ):
        self.slots, self.tasks, self.clock = slots, tasks, clock
        self.monotonic, self.wait = monotonic, wait

    def run(self, turn, operation, require_current):
        wait_until = self.monotonic() + min(10, max(0, turn["deadline"] - self.clock()))

        def require():
            if self.clock() >= turn["deadline"]:
                raise MeetError("meet_capacity_task_expired", 409)
            self.tasks.require_current(turn)
            require_current()

        require()
        identity = self.slots.reserve(turn, self.clock())
        dispatched = returned = False
        try:
            while True:
                require()
                if self.monotonic() >= wait_until:
                    raise MeetError("meet_capacity_wait_expired", 429)
                if self.slots.poll(identity, turn, self.clock()):
                    break
                self.wait(min(0.1, max(0, wait_until - self.monotonic())))
            require()
            if self.monotonic() >= wait_until:
                raise MeetError("meet_capacity_wait_expired", 429)
            dispatched = True
            result = operation()
            returned = True
            require()
            return result
        finally:
            # An HTTP error is not proof that GPU work stopped. Keep the slot
            # quarantined through the worker hard deadline plus cleanup grace.
            self.slots.finish(identity, turn, self.clock(), uncertain=dispatched and not returned)
