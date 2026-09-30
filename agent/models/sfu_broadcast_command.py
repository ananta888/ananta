"""Value types and errors of SFU broadcast user-intent commands.

The command outcome/error types are shared by the command service and the
durable command persistence; the persistence protocol lives in
:mod:`agent.ports.sfu_broadcast_command_repository`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


class SfuBroadcastCommandError(ValueError):
    def __init__(self, reason_code: str, status_code: int = 400) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class SfuBroadcastCommandResult:
    accepted: bool
    effective_version: int
    state: str
    reason_code: str
    command_ref: str
    replayed: bool = False

    def public(self) -> dict[str, object]:
        return {
            "ok": self.accepted,
            "accepted": self.accepted,
            "effective_version": self.effective_version,
            "state": self.state,
            "reason_code": self.reason_code,
            "command_ref": self.command_ref,
            "replayed": self.replayed,
        }


@dataclass(frozen=True, slots=True)
class SfuBroadcastCommandPolicyDecision:
    allowed: bool
    authorization_reason: str
    execution_reason: str
    policy_version: int
    admission_epoch: int | None = None
    membership_epoch: int | None = None


@dataclass(frozen=True, slots=True)
class SfuBroadcastCommandMutation:
    tenant_id: str
    room_id: str
    tenant_diagnostic_ref: str
    room_diagnostic_ref: str
    actor_diagnostic_ref: str
    actor_role: str
    operation_id: str
    request_digest: str
    action: str
    reason: str
    expected_version: int
    policy: SfuBroadcastCommandPolicyDecision
    data_saver: bool | None
    audio_only: bool | None
    quality_preference: str | None
    now: datetime
    retain_until: datetime


@dataclass(frozen=True, slots=True)
class SfuBroadcastCommandMutationResult:
    accepted: bool
    effective_version: int
    state: str
    reason_code: str
    audit_committed: bool = True
    replayed: bool = False


class SfuBroadcastCommandRepositoryError(RuntimeError):
    """Raised when mutation durability cannot be established."""


class SfuBroadcastCommandRepositoryConflict(SfuBroadcastCommandRepositoryError):
    """Raised if an operation identifier is reused for a different request."""


__all__ = [
    "SfuBroadcastCommandError",
    "SfuBroadcastCommandMutation",
    "SfuBroadcastCommandMutationResult",
    "SfuBroadcastCommandPolicyDecision",
    "SfuBroadcastCommandRepositoryConflict",
    "SfuBroadcastCommandRepositoryError",
    "SfuBroadcastCommandResult",
]
