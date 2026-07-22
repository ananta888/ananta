"""Fenced Hub state machine for SFU broadcast failover."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Protocol

from agent.services.sfu_broadcast_route_port import RuntimeControlModeV1


class SfuBroadcastFailoverError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class SfuFailoverState(str, Enum):
    PLANNED = "planned"
    REKEY_PENDING = "rekey_pending"
    ACTIVATING = "activating"
    COMPLETED = "completed"
    PARENT_FALLBACK = "parent_fallback"
    CONTROLLED_FAILED = "controlled_failed"


@dataclass(frozen=True, slots=True)
class SfuRuntimeBinding:
    runtime_control_mode: str
    cluster_id: str
    region: str
    runtime_instance_id: str | None

    def __post_init__(self) -> None:
        if not self.cluster_id or not self.region:
            raise ValueError("sfu_failover_target_scope_invalid")
        if self.runtime_control_mode == RuntimeControlModeV1.LIVEKIT_CONTROL_API.value:
            if self.runtime_instance_id is not None:
                raise ValueError("sfu_failover_native_node_scope_forbidden")
        elif (
            self.runtime_control_mode
            == RuntimeControlModeV1.AUTHENTICATED_RUNTIME_EXTENSION.value
        ):
            if not self.runtime_instance_id:
                raise ValueError("sfu_failover_runtime_instance_required")
        else:
            raise ValueError("sfu_failover_runtime_mode_unknown")


@dataclass(frozen=True, slots=True)
class SfuFailoverEpochs:
    route_epoch: int
    topology_epoch: int
    key_epoch: int
    fencing_token: int

    def __post_init__(self) -> None:
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in (
                self.route_epoch,
                self.topology_epoch,
                self.key_epoch,
                self.fencing_token,
            )
        ):
            raise ValueError("sfu_failover_epoch_invalid")


@dataclass(frozen=True, slots=True)
class SfuBroadcastFailoverRequest:
    decision_id: str
    tenant_id: str
    room_id: str
    source: SfuRuntimeBinding
    target: SfuRuntimeBinding | None
    current_epochs: SfuFailoverEpochs
    rekey_required: bool
    reason_code: str


@dataclass(frozen=True, slots=True)
class SfuBroadcastFailoverPolicy:
    failover_rto_seconds: float = 15.0
    retry_budget: int = 3
    retry_cooldown_seconds: float = 1.0
    token_ttl_seconds: float = 10.0

    def __post_init__(self) -> None:
        if (
            self.failover_rto_seconds <= 0
            or self.retry_budget < 0
            or self.retry_cooldown_seconds < 0
            or self.token_ttl_seconds <= 0
        ):
            raise ValueError("sfu_failover_policy_invalid")


@dataclass(frozen=True, slots=True)
class SfuParentRekeyResult:
    accepted: bool
    signed: bool
    key_epoch: int | None
    reason_code: str


@dataclass(frozen=True, slots=True)
class SfuScopedAdmissionToken:
    token_id: str
    binding: SfuRuntimeBinding
    epochs: SfuFailoverEpochs
    expires_at: float


@dataclass(frozen=True, slots=True)
class SfuRuntimeActivationAck:
    accepted: bool
    binding: SfuRuntimeBinding
    epochs: SfuFailoverEpochs
    reason_code: str


@dataclass(frozen=True, slots=True)
class SfuBroadcastFailoverRecord:
    decision_id: str
    request_digest: str
    state: SfuFailoverState
    epochs: SfuFailoverEpochs
    admission_revoked: bool
    token_id: str | None
    attempts: int
    started_at: float
    deadline_at: float
    next_retry_at: float
    reason_code: str
    version: int
    transitions: tuple[str, ...]

    @property
    def terminal(self) -> bool:
        return self.state in {
            SfuFailoverState.COMPLETED,
            SfuFailoverState.PARENT_FALLBACK,
            SfuFailoverState.CONTROLLED_FAILED,
        }


class SfuFailoverDecisionRepositoryPort(Protocol):
    def load_or_create(
        self, request: SfuBroadcastFailoverRequest, record: SfuBroadcastFailoverRecord
    ) -> SfuBroadcastFailoverRecord: ...
    def save(
        self, record: SfuBroadcastFailoverRecord, *, expected_version: int
    ) -> SfuBroadcastFailoverRecord: ...


class SfuOldAdmissionRevocationPort(Protocol):
    def revoke_old(
        self,
        request: SfuBroadcastFailoverRequest,
        successor: SfuFailoverEpochs,
        operation_id: str,
    ) -> bool: ...


class SfuParentRekeyPort(Protocol):
    def request_rekey(
        self,
        request: SfuBroadcastFailoverRequest,
        minimum_key_epoch: int,
        operation_id: str,
    ) -> SfuParentRekeyResult: ...


class SfuFailoverTokenPort(Protocol):
    def issue(
        self,
        request: SfuBroadcastFailoverRequest,
        binding: SfuRuntimeBinding,
        epochs: SfuFailoverEpochs,
        ttl_seconds: float,
        operation_id: str,
    ) -> SfuScopedAdmissionToken: ...


class SfuFailoverRouteActivationPort(Protocol):
    def activate(
        self,
        request: SfuBroadcastFailoverRequest,
        token: SfuScopedAdmissionToken,
        operation_id: str,
    ) -> SfuRuntimeActivationAck: ...


class SfuParentFallbackPort(Protocol):
    def activate_parent_fallback(
        self, request: SfuBroadcastFailoverRequest, operation_id: str
    ) -> bool: ...


class SfuFailoverGuardPort(Protocol):
    def kill_switch_enabled(self, tenant_id: str) -> bool: ...
    def authorization_revoked(self, tenant_id: str, room_id: str) -> bool: ...


class InMemorySfuFailoverDecisionRepository:
    def __init__(self) -> None:
        self._records: dict[str, SfuBroadcastFailoverRecord] = {}
        self._lock = threading.Lock()

    def load_or_create(
        self, request: SfuBroadcastFailoverRequest, record: SfuBroadcastFailoverRecord
    ) -> SfuBroadcastFailoverRecord:
        with self._lock:
            current = self._records.get(request.decision_id)
            if current is not None:
                if current.request_digest != record.request_digest:
                    raise SfuBroadcastFailoverError("sfu_failover_decision_conflict")
                return current
            self._records[request.decision_id] = record
            return record

    def save(
        self, record: SfuBroadcastFailoverRecord, *, expected_version: int
    ) -> SfuBroadcastFailoverRecord:
        with self._lock:
            current = self._records.get(record.decision_id)
            if current is None or current.version != expected_version:
                raise SfuBroadcastFailoverError("sfu_failover_version_conflict")
            if record.version != expected_version + 1:
                raise SfuBroadcastFailoverError("sfu_failover_version_invalid")
            self._records[record.decision_id] = record
            return record


class SfuBroadcastFailoverService:
    """Advances one bounded step per Hub reconciliation call."""

    def __init__(
        self,
        repository: SfuFailoverDecisionRepositoryPort,
        admission: SfuOldAdmissionRevocationPort,
        rekey: SfuParentRekeyPort,
        tokens: SfuFailoverTokenPort,
        routes: SfuFailoverRouteActivationPort,
        fallback: SfuParentFallbackPort,
        guards: SfuFailoverGuardPort,
        policy: SfuBroadcastFailoverPolicy | None = None,
        *,
        clock=time.time,
    ) -> None:
        self._repository = repository
        self._admission = admission
        self._rekey = rekey
        self._tokens = tokens
        self._routes = routes
        self._fallback = fallback
        self._guards = guards
        self._policy = policy or SfuBroadcastFailoverPolicy()
        self._clock = clock

    def start(self, request: SfuBroadcastFailoverRequest) -> SfuBroadcastFailoverRecord:
        now = float(self._clock())
        if not request.decision_id or not request.tenant_id or not request.room_id:
            raise SfuBroadcastFailoverError("sfu_failover_request_invalid")
        successor = SfuFailoverEpochs(
            route_epoch=request.current_epochs.route_epoch + 1,
            topology_epoch=request.current_epochs.topology_epoch + 1,
            key_epoch=request.current_epochs.key_epoch,
            fencing_token=request.current_epochs.fencing_token + 1,
        )
        record = SfuBroadcastFailoverRecord(
            decision_id=request.decision_id,
            request_digest=_request_digest(request),
            state=SfuFailoverState.PLANNED,
            epochs=successor,
            admission_revoked=False,
            token_id=None,
            attempts=0,
            started_at=now,
            deadline_at=now + self._policy.failover_rto_seconds,
            next_retry_at=now,
            reason_code=request.reason_code,
            version=1,
            transitions=(SfuFailoverState.PLANNED.value,),
        )
        return self._repository.load_or_create(request, record)

    def advance(
        self,
        request: SfuBroadcastFailoverRequest,
        record: SfuBroadcastFailoverRecord,
    ) -> SfuBroadcastFailoverRecord:
        current = self._repository.load_or_create(request, record)
        if current.terminal:
            return current
        now = float(self._clock())
        if now >= current.deadline_at:
            return self._fallback_terminal(request, current, "sfu_failover_rto_exceeded")
        if now < current.next_retry_at:
            return current
        if self._guards.kill_switch_enabled(request.tenant_id):
            return self._fallback_terminal(request, current, "sfu_failover_kill_switch")
        if self._guards.authorization_revoked(request.tenant_id, request.room_id):
            return self._fallback_terminal(request, current, "sfu_failover_authorization_revoked")
        if request.target is None:
            return self._fallback_terminal(request, current, "sfu_failover_target_missing")

        operation_id = f"failover:{request.decision_id}:{current.version + 1}"
        if current.state is SfuFailoverState.PLANNED:
            if not self._admission.revoke_old(request, current.epochs, operation_id):
                return self._retry(request, current, "sfu_failover_old_admission_revoke_failed")
            target = (
                SfuFailoverState.REKEY_PENDING
                if request.rekey_required
                else SfuFailoverState.ACTIVATING
            )
            return self._transition(
                current,
                target,
                admission_revoked=True,
                reason_code="sfu_failover_old_admission_revoked",
            )

        if current.state is SfuFailoverState.REKEY_PENDING:
            result = self._rekey.request_rekey(
                request, request.current_epochs.key_epoch + 1, operation_id
            )
            if (
                not result.accepted
                or not result.signed
                or result.key_epoch is None
                or result.key_epoch <= request.current_epochs.key_epoch
            ):
                return self._retry(request, current, result.reason_code)
            return self._transition(
                current,
                SfuFailoverState.ACTIVATING,
                epochs=replace(current.epochs, key_epoch=result.key_epoch),
                reason_code="sfu_failover_parent_rekey_accepted",
            )

        if current.state is SfuFailoverState.ACTIVATING:
            if not current.admission_revoked:
                return self._fallback_terminal(
                    request, current, "sfu_failover_admission_revoke_missing"
                )
            token = self._tokens.issue(
                request,
                request.target,
                current.epochs,
                self._policy.token_ttl_seconds,
                operation_id,
            )
            if (
                token.binding != request.target
                or token.epochs != current.epochs
                or not current.started_at < token.expires_at <= now + self._policy.token_ttl_seconds
            ):
                return self._retry(request, current, "sfu_failover_token_scope_invalid")
            ack = self._routes.activate(request, token, operation_id)
            if not self.ack_is_current(request, current, ack):
                return self._retry(request, current, ack.reason_code)
            return self._transition(
                current,
                SfuFailoverState.COMPLETED,
                token_id=token.token_id,
                reason_code="sfu_failover_completed",
            )
        return self._fallback_terminal(request, current, "sfu_failover_state_invalid")

    @staticmethod
    def ack_is_current(
        request: SfuBroadcastFailoverRequest,
        record: SfuBroadcastFailoverRecord,
        ack: SfuRuntimeActivationAck,
    ) -> bool:
        return (
            ack.accepted
            and request.target is not None
            and ack.binding == request.target
            and ack.epochs == record.epochs
            and ack.epochs.route_epoch > request.current_epochs.route_epoch
            and ack.epochs.topology_epoch > request.current_epochs.topology_epoch
            and ack.epochs.fencing_token > request.current_epochs.fencing_token
            and ack.epochs.key_epoch >= request.current_epochs.key_epoch
        )

    def _retry(
        self,
        request: SfuBroadcastFailoverRequest,
        current: SfuBroadcastFailoverRecord,
        reason_code: str,
    ) -> SfuBroadcastFailoverRecord:
        attempts = current.attempts + 1
        if attempts > self._policy.retry_budget:
            return self._fallback_terminal(
                request, current, "sfu_failover_retry_budget_exhausted"
            )
        now = float(self._clock())
        updated = replace(
            current,
            attempts=attempts,
            next_retry_at=now + self._policy.retry_cooldown_seconds,
            reason_code=reason_code,
            version=current.version + 1,
            transitions=current.transitions + (f"retry:{reason_code}",),
        )
        return self._repository.save(updated, expected_version=current.version)

    def _fallback_terminal(
        self,
        request: SfuBroadcastFailoverRequest,
        current: SfuBroadcastFailoverRecord,
        reason_code: str,
    ) -> SfuBroadcastFailoverRecord:
        operation_id = f"failover-fallback:{request.decision_id}:{current.version + 1}"
        accepted = self._fallback.activate_parent_fallback(request, operation_id)
        state = (
            SfuFailoverState.PARENT_FALLBACK
            if accepted
            else SfuFailoverState.CONTROLLED_FAILED
        )
        return self._transition(current, state, reason_code=reason_code)

    def _transition(
        self,
        current: SfuBroadcastFailoverRecord,
        state: SfuFailoverState,
        *,
        epochs: SfuFailoverEpochs | None = None,
        admission_revoked: bool | None = None,
        token_id: str | None = None,
        reason_code: str,
    ) -> SfuBroadcastFailoverRecord:
        updated = replace(
            current,
            state=state,
            epochs=current.epochs if epochs is None else epochs,
            admission_revoked=(
                current.admission_revoked
                if admission_revoked is None
                else admission_revoked
            ),
            token_id=current.token_id if token_id is None else token_id,
            reason_code=reason_code,
            version=current.version + 1,
            transitions=current.transitions + (state.value,),
        )
        return self._repository.save(updated, expected_version=current.version)


def _request_digest(request: SfuBroadcastFailoverRequest) -> str:
    return hashlib.sha256(
        json.dumps(asdict(request), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


__all__ = [
    "InMemorySfuFailoverDecisionRepository",
    "SfuBroadcastFailoverError",
    "SfuBroadcastFailoverPolicy",
    "SfuBroadcastFailoverRecord",
    "SfuBroadcastFailoverRequest",
    "SfuBroadcastFailoverService",
    "SfuFailoverEpochs",
    "SfuFailoverState",
    "SfuParentRekeyResult",
    "SfuRuntimeActivationAck",
    "SfuRuntimeBinding",
    "SfuScopedAdmissionToken",
]
