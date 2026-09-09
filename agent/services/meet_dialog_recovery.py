"""Hub-owned reconnect admission; Workers execute one bounded issued handoff."""

import time
from typing import Protocol

from agent.models.meet_dialog_recovery import RecoveryMembership, RecoveryOwner
from agent.models.meet_recovery_binding import recovery_owner
from agent.services.meet_contract import MeetError
from ananta_contracts.meet_reconnect import MAX_RECOVERIES


class RecoveryStatePort(Protocol):
    def observe(self, owner: RecoveryOwner, membership: RecoveryMembership, now_ms: int): ...
    def reserve(self, owner: RecoveryOwner, session_id: str, now_ms: int): ...
    def retired(self, owner: RecoveryOwner, attempt: int, session_id: str, now_ms: int): ...
    def take_grant(self, owner: RecoveryOwner, attempt: int, session_id: str, now_ms: int): ...
    def pending(self, owner: RecoveryOwner, attempt: int, session_id: str, now_ms: int): ...


class RecoveryAuthorityPort(Protocol):
    def current(self, task_id: str, lease_id: str, runtime_id: str): ...


class RetirementPort(Protocol):
    def retire(self, task_id: str, lease_id: str, runtime_id: str, session_id: str): ...


class RecoveryPhasePort(Protocol):
    def begin_recovery(self, scope, session_id: str, attempt: int): ...


class RecoveryGrantPort(Protocol):
    def issue_dialog(self, authority, task_id: str, lease_id: str, runtime_id: str, now: float): ...


class MeetDialogRecovery:
    def __init__(
        self,
        authority: RecoveryAuthorityPort,
        states: RecoveryStatePort,
        meet: RetirementPort,
        issuer: RecoveryGrantPort,
        phases: RecoveryPhasePort,
        *,
        speaker_floor=None,
        clock=time.time,
    ):
        self.authority, self.states, self.meet = authority, states, meet
        self.issuer, self.phases, self.speaker_floor, self.clock = issuer, phases, speaker_floor, clock

    def _now(self):
        return int(self.clock() * 1000)

    def _current(self, ids, expected=None):
        scope = self.authority.current(*ids)
        owner = recovery_owner(scope)
        if expected is not None and owner != expected:
            raise MeetError("meet_reconnect_authority_changed", 409)
        if scope.speaker_floor and self.speaker_floor is None:
            raise MeetError("meet_reconnect_speaker_coordinator_required", 409)
        return scope, owner

    def observe(self, scope, receipt):
        """Called only after the TLS adapter has validated current Meet bindings."""
        ids = scope.task_id, scope.lease_id, scope.runtime_id
        owner = recovery_owner(scope)
        self._current(ids, owner)
        self.states.observe(owner, RecoveryMembership.from_receipt(receipt), self._now())
        self._current(ids, owner)

    def _begin(self, ids, scope, owner, session_id):
        reserved = self.states.reserve(owner, session_id, self._now())
        self._current(ids, owner)
        if scope.speaker_floor:
            self.speaker_floor.withdraw(scope)
        self.meet.retire(*ids, session_id)
        scope, _ = self._current(ids, owner)
        # A failed phase CAS must leave the resource in `retiring`: no later
        # poll can issue a grant based solely on an uncertain partial operation.
        self.phases.begin_recovery(scope, session_id, reserved["attempt"])
        self._current(ids, owner)
        return self.states.retired(owner, reserved["attempt"], session_id, self._now())

    def exchange(self, payload):
        attempt = payload.get("attempt")
        if type(attempt) is not int or not 0 <= attempt <= MAX_RECOVERIES:
            raise MeetError("meet_reconnect_attempt_invalid", 409)
        ids = tuple(payload[key] for key in ("task_id", "lease_id", "runtime_id"))
        session_id = payload["meet_session_id"]
        scope, owner = self._current(ids)
        meeting = None
        if attempt == 0:
            state = self._begin(ids, scope, owner, session_id)
        else:
            state = self.states.pending(owner, attempt, session_id, self._now())
            if state["state"] == "waiting" and self._now() >= state["ready_ms"]:
                self._current(ids, owner)
                self.states.take_grant(owner, attempt, session_id, self._now())
                meeting = self.issuer.issue_dialog(self.authority, *ids, self.clock())
                state = self.states.pending(owner, attempt, session_id, self._now())
        self._current(ids, owner)
        state = self.states.pending(owner, state["attempt"], session_id, self._now())
        if state["state"] not in {"waiting", "joining"}:
            raise MeetError("meet_reconnect_retirement_unconfirmed", 409)
        return {
            "schema": "ananta.meet-reconnect-state.v1",
            "nonce": payload["nonce"],
            **{key: state[key] for key in ("attempt", "state", "deadline_ms", "ready_ms")},
            "meeting": meeting,
        }
