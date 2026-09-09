"""Bridge native dialog authority and the Hub's durable speaker-resource port."""

import time
from typing import Protocol

from agent.models.meet_speaker_floor import CLEANUP_MS, SpeakerOwner, SpeakerTurn
from agent.services.meet_contract import MeetError
from agent.services.meet_speaker_floor import MeetSpeakerFloor
from ananta_contracts.meet_speaker_floor import validate_speaker_permit


class SpeakerStatePort(Protocol):
    def projection(self, owner: SpeakerOwner, now_ms: int): ...
    def complete(self, owner: SpeakerOwner, permit: dict, now_ms: int): ...
    def revoke(self, owner: SpeakerOwner, now_ms: int): ...


def require_speaker_mode(scope, coordinator):
    required = coordinator is not None and "speech.publish" in scope.capabilities
    if scope.speaker_floor is not required:
        raise MeetError("meet_speaker_negotiation_required", 409)


def negotiated_speaker_fields(enabled, capabilities):
    return {"speaker_floor": True} if enabled and "speech.publish" in capabilities else {}


class MeetDialogSpeakerFloor:
    def __init__(
        self,
        admission: MeetSpeakerFloor,
        states: SpeakerStatePort,
        *,
        clock=time.time,
        monotonic=time.monotonic,
        priority=None,
    ):
        self.admission, self.states, self.clock = admission, states, clock
        self.monotonic, self.ready_at = monotonic, monotonic() + CLEANUP_MS / 1000
        self.priority = priority if priority is not None else lambda _scope: 0

    def require_ready(self):
        if self.monotonic() < self.ready_at:
            raise MeetError("meet_speaker_startup_quarantine", 503)

    def authorize_reply(self, scope, reservation, response, current):
        require_speaker_mode(scope, self)
        self.require_ready()
        binding = response["reply"]["binding"]
        turn = SpeakerTurn.from_binding(scope.origin, binding, reservation.intent_id, priority=self.priority(scope))
        if turn.owner != SpeakerOwner.from_scope(scope):
            raise MeetError("meet_speaker_turn_changed", 409)

        def require_current():
            active = current.current(scope.session_id)
            if active is None or active.scope != reservation.scope:
                raise MeetError("meet_spoken_authority_changed", 409)

        permit = self.admission.acquire(turn, require_current)
        try:
            permit = validate_speaker_permit(permit, deadline_ms=binding["deadline_ms"])
            self.admission.require_current(turn, permit, require_current)
            return response | {"reply": response["reply"] | {"speaker_floor": permit}}
        except Exception:
            self.admission.finish(turn, permit)
            raise

    def exchange(self, scope, completed=None):
        require_speaker_mode(scope, self)
        owner, now = SpeakerOwner.from_scope(scope), int(self.clock() * 1000)
        if completed is not None:
            self.states.complete(owner, completed, now)
        speech = scope.controls.speech
        if speech is None or not speech.enabled or not scope.controls.chat.enabled or self.monotonic() < self.ready_at:
            self.states.revoke(owner, now)
            return None
        return self.states.projection(owner, now)

    def withdraw(self, scope):
        require_speaker_mode(scope, self)
        self.states.revoke(SpeakerOwner.from_scope(scope), int(self.clock() * 1000))
