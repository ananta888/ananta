"""Bounded Hub speaker admission; no Worker scheduling or media publication."""

import time
from typing import Protocol

from agent.models.meet_speaker_floor import WAIT_MS, SpeakerTurn
from agent.services.meet_contract import MeetError


class SpeakerFloorPort(Protocol):
    def reserve(self, turn: SpeakerTurn, now_ms: int): ...
    def poll(self, turn: SpeakerTurn, now_ms: int): ...
    def current(self, turn: SpeakerTurn, permit: dict, now_ms: int): ...
    def cancel(self, turn: SpeakerTurn, now_ms: int): ...


class MeetSpeakerFloor:
    def __init__(self, store: SpeakerFloorPort, *, clock=time.time, monotonic=time.monotonic, wait=time.sleep):
        self.store, self.clock, self.monotonic, self.wait = store, clock, monotonic, wait

    def _now(self):
        return int(self.clock() * 1000)

    def acquire(self, turn, require_current):
        """Reserve one resource for an already admitted Hub turn, not a new task."""
        end = self.monotonic() + min(WAIT_MS, max(0, turn.deadline_ms - self._now())) / 1000

        def require():
            if self._now() >= turn.deadline_ms or self.monotonic() >= end:
                raise MeetError("meet_speaker_wait_expired", 429)
            require_current()

        require()
        self.store.reserve(turn, self._now())
        try:
            while True:
                require()
                permit = self.store.poll(turn, self._now())
                if permit is not None:
                    require()  # A grant cannot hide authority changes during SQL admission.
                    if not self.store.current(turn, permit, self._now()):
                        raise MeetError("meet_speaker_permit_changed", 409)
                    return permit
                self.wait(min(0.1, max(0, end - self.monotonic())))
        except Exception:
            # Cancellation is exact and bounded. If storage is unavailable, its
            # existing durable deadline still quarantines any uncertain grant.
            self.store.cancel(turn, self._now())
            raise

    def require_current(self, turn, permit, require_authority):
        try:
            require_authority()
            if not self.store.current(turn, permit, self._now()):
                raise MeetError("meet_speaker_permit_changed", 409)
            require_authority()
        except Exception:
            self.store.cancel(turn, self._now())
            raise

    def interrupt(self, turn, permit, require_allowed):
        """Only explicit current Hub policy can authorize headless barge-in."""
        require_allowed()
        if not self.store.current(turn, permit, self._now()):
            return False
        # The policy port includes current task/principal ownership. Neither a
        # transcript nor a Worker-selected priority is passed through this seam.
        require_allowed()
        self.store.cancel(turn, self._now())
        return True

    def finish(self, turn, permit):
        if not self.store.current(turn, permit, self._now()):
            return False
        self.store.cancel(turn, self._now())
        return True
