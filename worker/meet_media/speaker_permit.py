"""Execute one Hub speech permit; never choose speakers or schedule tasks."""

import time

from ananta_contracts.meet_speaker_floor import validate_speaker_permit


class SpeakerPermitGate:
    def __init__(self, enabled, *, clock=time.time, finished=None):
        if type(enabled) is not bool:
            raise ValueError("meet_speaker_negotiation_invalid")
        self.enabled, self.clock, self.finished = enabled, clock, finished
        self.current = self.active = None
        self.retired_sequence = 0

    def update(self, permit):
        if permit is not None and not self.enabled:
            raise ValueError("meet_speaker_not_negotiated")
        self.current = None if permit is None else validate_speaker_permit(permit)

    def accept(self, permit, binding_deadline):
        if not self.enabled:
            if permit is not None:
                raise ValueError("meet_speaker_not_negotiated")
            return
        permit = validate_speaker_permit(permit, deadline_ms=binding_deadline)
        if (
            self.active is not None
            or permit != self.current
            or permit["sequence"] <= self.retired_sequence
            or self.clock() * 1000 >= permit["expires_ms"]
        ):
            raise ValueError("meet_speaker_permit_changed")
        self.active = permit

    def deadline(self, binding_deadline):
        if not self.enabled:
            return binding_deadline
        if (
            self.active is None
            or self.active != self.current
            or self.clock() * 1000 >= self.active["expires_ms"]
            or self.active["expires_ms"] > binding_deadline
        ):
            raise ValueError("meet_speaker_permit_changed")
        return self.active["expires_ms"]

    def close(self, *, report=True):
        permit, self.active = self.active, None
        if permit is not None:
            self.retired_sequence = max(self.retired_sequence, permit["sequence"])
            if report and self.finished is not None:
                self.finished(dict(permit))  # Only a local completion handoff, never an HTTP wait.
