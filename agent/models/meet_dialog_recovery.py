"""Immutable recovery resource identity and already-validated membership metadata."""

import re
from dataclasses import asdict, dataclass

from agent.services.meet_contract import MeetError
from ananta_contracts.meet_reconnect import MAX_RECOVERIES as MAX_RECOVERIES
from ananta_contracts.meet_reconnect import RECOVERY_MS as RECOVERY_MS
from ananta_contracts.meet_reconnect import RECOVERY_QUIET_MS as RECOVERY_QUIET_MS


def instant(value):
    if type(value) is not int or not 0 < value < 2**53:
        raise MeetError("meet_recovery_clock_invalid", 409)
    return value


@dataclass(frozen=True)
class RecoveryOwner:
    task_id: str
    assignment_digest: str
    deadline_ms: int

    def __post_init__(self):
        if (
            not isinstance(self.task_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", self.task_id)
            or not isinstance(self.assignment_digest, str)
            or not re.fullmatch(r"[a-f0-9]{64}", self.assignment_digest)
        ):
            raise MeetError("meet_recovery_binding_invalid", 409)
        instant(self.deadline_ms)


@dataclass(frozen=True)
class RecoveryMembership:
    session_id: str
    peer_id: str
    generation: int
    epoch: int
    expires_ms: int
    absolute_expires_ms: int

    def __post_init__(self):
        if (
            not isinstance(self.session_id, str)
            or not re.fullmatch(r"ms_[A-Za-z0-9_-]{32}", self.session_id)
            or not isinstance(self.peer_id, str)
            or not re.fullmatch(r"[a-f0-9]{16}", self.peer_id)
            or type(self.generation) is not int
            or not 1 <= self.generation <= 512
        ):
            raise MeetError("meet_recovery_membership_invalid", 409)
        for value in (self.epoch, self.expires_ms, self.absolute_expires_ms):
            instant(value)
        if self.expires_ms > self.absolute_expires_ms:
            raise MeetError("meet_recovery_membership_invalid", 409)

    @classmethod
    def from_receipt(cls, receipt):
        # The service must validate the authenticated Meet receipt first.
        lease = receipt["lease"]
        return cls(
            lease["sessionId"],
            receipt["peerId"],
            lease["generation"],
            receipt["membershipEpoch"],
            lease["expiresAt"],
            lease["absoluteExpiresAt"],
        )

    @property
    def metadata(self):
        return asdict(self)

    def require_current(self, owner, now_ms):
        instant(now_ms)
        if not now_ms < self.expires_ms <= owner.deadline_ms:
            raise MeetError("meet_recovery_membership_expired", 409)

    def require_refresh(self, previous):
        if (
            self.session_id != previous.session_id
            or self.peer_id != previous.peer_id
            or self.generation < previous.generation
            or self.epoch < previous.epoch
            or self.expires_ms < previous.expires_ms
            or self.absolute_expires_ms != previous.absolute_expires_ms
            or self.generation == previous.generation
            and self.expires_ms != previous.expires_ms
            or self.generation > previous.generation
            and self.expires_ms <= previous.expires_ms
        ):
            raise MeetError("meet_recovery_membership_changed", 409)


def validate_recovery_record(row):
    """Fail closed on malformed persisted state before it can consume authority."""
    try:
        membership = RecoveryMembership(**row["membership"])
        attempt, retired, state = row["attempt"], row["retired"], row["state"]
        if (
            type(attempt) is not int
            or not 0 <= attempt <= MAX_RECOVERIES
            or type(retired) is not list
            or len(retired) != attempt
            or state not in {"active", "retiring", "waiting", "joining", "failed"}
        ):
            raise ValueError()
        sessions, peers = set(), set()
        for old in retired:
            if type(old) is not dict or set(old) != {"session_id", "peer_id"}:
                raise ValueError()
            checked = RecoveryMembership(old["session_id"], old["peer_id"], 1, 1, 1, 1)
            if checked.session_id in sessions or checked.peer_id in peers:
                raise ValueError()
            sessions.add(checked.session_id)
            peers.add(checked.peer_id)
        instant(row["last_now"])
        for field in ("attempt_until", "ready_at"):
            if type(row[field]) is not int or not 0 <= row[field] < 2**53:
                raise ValueError()
        if state == "active":
            if (
                row["attempt_until"]
                or row["ready_at"]
                or membership.session_id in sessions
                or membership.peer_id in peers
            ):
                raise ValueError()
        elif state != "failed":
            if (
                not attempt
                or not row["attempt_until"]
                or row["attempt_until"] > row["deadline_ms"]
                or retired[-1] != {"session_id": membership.session_id, "peer_id": membership.peer_id}
                or (state == "retiring") != (row["ready_at"] == 0)
            ):
                raise ValueError()
        return membership
    except (KeyError, TypeError, ValueError, MeetError):
        raise MeetError("meet_recovery_record_invalid", 409) from None
