"""Persistence port for atomic SFU broadcast user-intent mutations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


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


class SfuBroadcastCommandRepositoryPort(Protocol):
    def execute(
        self, mutation: SfuBroadcastCommandMutation
    ) -> SfuBroadcastCommandMutationResult: ...

    def purge_expired(self, *, now: datetime | None = None) -> int: ...
