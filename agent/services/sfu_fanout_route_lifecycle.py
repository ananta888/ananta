"""Explicit, pure Hub state machine for one fenced fanout route."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from enum import Enum

from agent.services.sfu_broadcast_route_port import RuntimeControlModeV1


class FanoutRouteState(str, Enum):
    INTENT = "intent"
    DISPATCH = "dispatch"
    ACK = "ack"
    ACTIVE = "active"
    UPDATE = "update"
    REVOKE = "revoke"
    EXPIRE = "expire"
    FAILED = "failed"


class FanoutRouteEvent(str, Enum):
    DISPATCH = "dispatch"
    ACK = "ack"
    ACTIVATE = "activate"
    UPDATE = "update"
    REVOKE = "revoke"
    EXPIRE = "expire"
    FAIL = "fail"
    TIMEOUT = "timeout"
    RETRY = "retry"


class FanoutRouteReason(str, Enum):
    TRANSITION_APPLIED = "route_transition_applied"
    DUPLICATE_IDEMPOTENT = "route_duplicate_idempotent"
    OPERATION_CONFLICT = "route_operation_conflict"
    TRANSITION_FORBIDDEN = "route_transition_forbidden"
    TERMINAL = "route_terminal"
    DEADLINE_NOT_REACHED = "route_deadline_not_reached"
    RETRY_BUDGET_EXHAUSTED = "route_retry_budget_exhausted"
    RETRY_COOLDOWN = "route_retry_cooldown"
    INTENT_NOT_PERSISTED = "route_intent_not_persisted"
    EPOCH_STALE = "route_epoch_stale"
    FENCING_STALE = "route_fencing_stale"
    PARENT_SUBSCRIPTION_INACTIVE = "route_parent_subscription_inactive"
    APPLY_EVIDENCE_MISSING = "route_apply_evidence_missing"
    APPLY_EVIDENCE_BINDING_INVALID = "route_apply_evidence_binding_invalid"
    APPLY_EVIDENCE_EXPIRED = "route_apply_evidence_expired"
    APPLY_EVIDENCE_REORDERED = "route_apply_evidence_reordered"
    LIVEKIT_API_PROOF_INVALID = "route_livekit_api_proof_invalid"
    RUNTIME_ACK_INVALID = "route_runtime_ack_invalid"
    ROUTE_EXPIRED = "route_expired"


TERMINAL_ROUTE_STATES = frozenset(
    {FanoutRouteState.REVOKE, FanoutRouteState.EXPIRE, FanoutRouteState.FAILED}
)

TRANSITION_TABLE: dict[
    tuple[FanoutRouteState, FanoutRouteEvent], FanoutRouteState
] = {
    (FanoutRouteState.INTENT, FanoutRouteEvent.DISPATCH): FanoutRouteState.DISPATCH,
    (FanoutRouteState.DISPATCH, FanoutRouteEvent.ACK): FanoutRouteState.ACK,
    (FanoutRouteState.ACK, FanoutRouteEvent.ACTIVATE): FanoutRouteState.ACTIVE,
    (FanoutRouteState.ACTIVE, FanoutRouteEvent.UPDATE): FanoutRouteState.UPDATE,
    (FanoutRouteState.UPDATE, FanoutRouteEvent.ACK): FanoutRouteState.ACK,
}
for _state in (
    FanoutRouteState.INTENT,
    FanoutRouteState.DISPATCH,
    FanoutRouteState.ACK,
    FanoutRouteState.ACTIVE,
    FanoutRouteState.UPDATE,
):
    TRANSITION_TABLE[(_state, FanoutRouteEvent.REVOKE)] = FanoutRouteState.REVOKE
    TRANSITION_TABLE[(_state, FanoutRouteEvent.EXPIRE)] = FanoutRouteState.EXPIRE
    TRANSITION_TABLE[(_state, FanoutRouteEvent.FAIL)] = FanoutRouteState.FAILED


@dataclass(frozen=True, slots=True)
class FanoutRouteLifecycleConfig:
    dispatch_deadline_ms: int = 2_000
    update_deadline_ms: int = 2_000
    retry_budget: int = 3
    retry_cooldown_ms: int = 250
    receipt_history_max: int = 64


@dataclass(frozen=True, slots=True)
class RouteEpochBinding:
    policy_epoch: int
    membership_epoch: int
    key_epoch: int
    route_epoch: int
    topology_epoch: int


@dataclass(frozen=True, slots=True)
class RouteActivationGuards:
    epochs_current: bool
    fencing_current: bool
    parent_subscription_active: bool


@dataclass(frozen=True, slots=True)
class RouteApplyEvidence:
    operation_id: str
    idempotency_key: str
    nonce: str
    sequence: int
    projection_version: int
    expires_at_ms: int
    tenant_id: str
    room_ref: str
    runtime_scope_ref: str
    intent_digest: str
    fencing_token: str
    route_epoch: int
    runtime_control_mode: RuntimeControlModeV1
    tls_bound: bool = False
    api_credential_bound: bool = False
    reconciliation_confirmed: bool = False
    mtls_bound: bool = False
    signature_verified: bool = False


@dataclass(frozen=True, slots=True)
class FanoutRouteLifecycleRecord:
    route_id: str
    tenant_id: str
    room_ref: str
    runtime_scope_ref: str
    runtime_control_mode: RuntimeControlModeV1
    state: FanoutRouteState
    intent_persisted: bool
    intent_digest: str
    idempotency_key: str
    nonce: str
    intent_sequence: int
    projection_version: int
    epochs: RouteEpochBinding
    fencing_token: str
    issued_at_ms: int
    expires_at_ms: int
    deadline_at_ms: int | None
    retry_count: int
    cooldown_until_ms: int | None
    apply_proof_digest: str | None
    last_ack_sequence: int
    receipts: tuple[tuple[str, str], ...]

    @classmethod
    def persisted_intent(
        cls,
        *,
        route_id: str,
        tenant_id: str,
        room_ref: str,
        runtime_scope_ref: str,
        runtime_control_mode: RuntimeControlModeV1,
        intent_digest: str,
        idempotency_key: str,
        nonce: str,
        intent_sequence: int,
        projection_version: int,
        epochs: RouteEpochBinding,
        fencing_token: str,
        issued_at_ms: int,
        expires_at_ms: int,
    ) -> "FanoutRouteLifecycleRecord":
        return cls(
            route_id=route_id,
            tenant_id=tenant_id,
            room_ref=room_ref,
            runtime_scope_ref=runtime_scope_ref,
            runtime_control_mode=runtime_control_mode,
            state=FanoutRouteState.INTENT,
            intent_persisted=True,
            intent_digest=intent_digest,
            idempotency_key=idempotency_key,
            nonce=nonce,
            intent_sequence=intent_sequence,
            projection_version=projection_version,
            epochs=epochs,
            fencing_token=fencing_token,
            issued_at_ms=issued_at_ms,
            expires_at_ms=expires_at_ms,
            deadline_at_ms=None,
            retry_count=0,
            cooldown_until_ms=None,
            apply_proof_digest=None,
            last_ack_sequence=0,
            receipts=(),
        )


@dataclass(frozen=True, slots=True)
class FanoutRouteTransitionResult:
    record: FanoutRouteLifecycleRecord
    accepted: bool
    replayed: bool
    reason_code: FanoutRouteReason
    audit_digest: str


class SfuFanoutRouteLifecycle:
    def __init__(self, config: FanoutRouteLifecycleConfig) -> None:
        self._config = config

    def transition(
        self,
        record: FanoutRouteLifecycleRecord,
        event: FanoutRouteEvent,
        *,
        operation_id: str,
        request_digest: str,
        now_ms: int,
        guards: RouteActivationGuards | None = None,
        evidence: RouteApplyEvidence | None = None,
    ) -> FanoutRouteTransitionResult:
        audit = self._audit_digest(record, event, operation_id, request_digest, now_ms)
        receipts = dict(record.receipts)
        if operation_id in receipts:
            reason = (
                FanoutRouteReason.DUPLICATE_IDEMPOTENT
                if receipts[operation_id] == request_digest
                else FanoutRouteReason.OPERATION_CONFLICT
            )
            return FanoutRouteTransitionResult(
                record,
                receipts[operation_id] == request_digest,
                receipts[operation_id] == request_digest,
                reason,
                audit,
            )
        if record.state in TERMINAL_ROUTE_STATES:
            return FanoutRouteTransitionResult(
                record, False, False, FanoutRouteReason.TERMINAL, audit
            )
        if now_ms >= record.expires_at_ms:
            return self._accepted(
                replace(record, state=FanoutRouteState.EXPIRE),
                operation_id,
                request_digest,
                FanoutRouteReason.ROUTE_EXPIRED,
                audit,
            )
        if event is FanoutRouteEvent.TIMEOUT:
            return self._timeout(
                record, operation_id, request_digest, now_ms, audit
            )
        if event is FanoutRouteEvent.RETRY:
            return self._retry(record, operation_id, request_digest, now_ms, audit)
        target = TRANSITION_TABLE.get((record.state, event))
        if target is None:
            return FanoutRouteTransitionResult(
                record,
                False,
                False,
                FanoutRouteReason.TRANSITION_FORBIDDEN,
                audit,
            )
        if not record.intent_persisted:
            return FanoutRouteTransitionResult(
                record,
                False,
                False,
                FanoutRouteReason.INTENT_NOT_PERSISTED,
                audit,
            )
        next_record = replace(record, state=target)
        if event is FanoutRouteEvent.DISPATCH:
            next_record = replace(
                next_record,
                deadline_at_ms=now_ms + self._config.dispatch_deadline_ms,
            )
        elif event is FanoutRouteEvent.UPDATE:
            next_record = replace(
                next_record,
                deadline_at_ms=now_ms + self._config.update_deadline_ms,
                apply_proof_digest=None,
            )
        elif event is FanoutRouteEvent.ACK:
            reason = self._evidence_reason(record, evidence, operation_id, now_ms)
            if reason is not None:
                return FanoutRouteTransitionResult(
                    record, False, False, reason, audit
                )
            assert evidence is not None
            next_record = replace(
                next_record,
                deadline_at_ms=None,
                apply_proof_digest=_canonical_digest(asdict(evidence)),
                last_ack_sequence=evidence.sequence,
            )
        elif event is FanoutRouteEvent.ACTIVATE:
            reason = self._activation_reason(record, guards)
            if reason is not None:
                return FanoutRouteTransitionResult(
                    record, False, False, reason, audit
                )
            next_record = replace(
                next_record,
                deadline_at_ms=None,
                retry_count=0,
                cooldown_until_ms=None,
            )
        return self._accepted(
            next_record,
            operation_id,
            request_digest,
            FanoutRouteReason.TRANSITION_APPLIED,
            audit,
        )

    def _activation_reason(
        self,
        record: FanoutRouteLifecycleRecord,
        guards: RouteActivationGuards | None,
    ) -> FanoutRouteReason | None:
        if not record.intent_persisted:
            return FanoutRouteReason.INTENT_NOT_PERSISTED
        if record.apply_proof_digest is None:
            return FanoutRouteReason.APPLY_EVIDENCE_MISSING
        if guards is None or not guards.epochs_current:
            return FanoutRouteReason.EPOCH_STALE
        if not guards.fencing_current:
            return FanoutRouteReason.FENCING_STALE
        if not guards.parent_subscription_active:
            return FanoutRouteReason.PARENT_SUBSCRIPTION_INACTIVE
        return None

    def _evidence_reason(
        self,
        record: FanoutRouteLifecycleRecord,
        evidence: RouteApplyEvidence | None,
        operation_id: str,
        now_ms: int,
    ) -> FanoutRouteReason | None:
        if evidence is None:
            return FanoutRouteReason.APPLY_EVIDENCE_MISSING
        if evidence.expires_at_ms <= now_ms or evidence.expires_at_ms > record.expires_at_ms:
            return FanoutRouteReason.APPLY_EVIDENCE_EXPIRED
        if evidence.sequence <= record.last_ack_sequence:
            return FanoutRouteReason.APPLY_EVIDENCE_REORDERED
        bindings_match = (
            evidence.operation_id == operation_id
            and evidence.idempotency_key == record.idempotency_key
            and evidence.nonce == record.nonce
            and evidence.sequence == record.intent_sequence
            and evidence.projection_version == record.projection_version
            and evidence.tenant_id == record.tenant_id
            and evidence.room_ref == record.room_ref
            and evidence.runtime_scope_ref == record.runtime_scope_ref
            and evidence.intent_digest == record.intent_digest
            and evidence.fencing_token == record.fencing_token
            and evidence.route_epoch == record.epochs.route_epoch
            and evidence.runtime_control_mode is record.runtime_control_mode
        )
        if not bindings_match:
            return FanoutRouteReason.APPLY_EVIDENCE_BINDING_INVALID
        if record.runtime_control_mode is RuntimeControlModeV1.LIVEKIT_CONTROL_API:
            if not (
                evidence.tls_bound
                and evidence.api_credential_bound
                and evidence.reconciliation_confirmed
            ):
                return FanoutRouteReason.LIVEKIT_API_PROOF_INVALID
        elif not (evidence.mtls_bound or evidence.signature_verified):
            return FanoutRouteReason.RUNTIME_ACK_INVALID
        return None

    def _timeout(
        self,
        record: FanoutRouteLifecycleRecord,
        operation_id: str,
        request_digest: str,
        now_ms: int,
        audit: str,
    ) -> FanoutRouteTransitionResult:
        if record.state not in (FanoutRouteState.DISPATCH, FanoutRouteState.UPDATE):
            return FanoutRouteTransitionResult(
                record,
                False,
                False,
                FanoutRouteReason.TRANSITION_FORBIDDEN,
                audit,
            )
        if record.deadline_at_ms is None or now_ms < record.deadline_at_ms:
            return FanoutRouteTransitionResult(
                record,
                False,
                False,
                FanoutRouteReason.DEADLINE_NOT_REACHED,
                audit,
            )
        if record.retry_count >= self._config.retry_budget:
            return self._accepted(
                replace(record, state=FanoutRouteState.FAILED),
                operation_id,
                request_digest,
                FanoutRouteReason.RETRY_BUDGET_EXHAUSTED,
                audit,
            )
        return self._accepted(
            replace(
                record,
                retry_count=record.retry_count + 1,
                cooldown_until_ms=now_ms + self._config.retry_cooldown_ms,
                deadline_at_ms=None,
            ),
            operation_id,
            request_digest,
            FanoutRouteReason.TRANSITION_APPLIED,
            audit,
        )

    def _retry(
        self,
        record: FanoutRouteLifecycleRecord,
        operation_id: str,
        request_digest: str,
        now_ms: int,
        audit: str,
    ) -> FanoutRouteTransitionResult:
        if record.state not in (FanoutRouteState.DISPATCH, FanoutRouteState.UPDATE):
            return FanoutRouteTransitionResult(
                record,
                False,
                False,
                FanoutRouteReason.TRANSITION_FORBIDDEN,
                audit,
            )
        if record.cooldown_until_ms is None or now_ms < record.cooldown_until_ms:
            return FanoutRouteTransitionResult(
                record, False, False, FanoutRouteReason.RETRY_COOLDOWN, audit
            )
        deadline = (
            self._config.dispatch_deadline_ms
            if record.state is FanoutRouteState.DISPATCH
            else self._config.update_deadline_ms
        )
        return self._accepted(
            replace(
                record,
                deadline_at_ms=now_ms + deadline,
                cooldown_until_ms=None,
            ),
            operation_id,
            request_digest,
            FanoutRouteReason.TRANSITION_APPLIED,
            audit,
        )

    def _accepted(
        self,
        record: FanoutRouteLifecycleRecord,
        operation_id: str,
        request_digest: str,
        reason: FanoutRouteReason,
        audit: str,
    ) -> FanoutRouteTransitionResult:
        receipts = (*record.receipts, (operation_id, request_digest))
        updated = replace(
            record, receipts=receipts[-self._config.receipt_history_max :]
        )
        return FanoutRouteTransitionResult(updated, True, False, reason, audit)

    @staticmethod
    def _audit_digest(
        record: FanoutRouteLifecycleRecord,
        event: FanoutRouteEvent,
        operation_id: str,
        request_digest: str,
        now_ms: int,
    ) -> str:
        return _canonical_digest(
            {
                "domain": "ananta:sfu-route-lifecycle-audit:v1",
                "route_id": record.route_id,
                "state": record.state.value,
                "event": event.value,
                "operation_id": operation_id,
                "request_digest": request_digest,
                "now_ms": now_ms,
            }
        )


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "FanoutRouteEvent",
    "FanoutRouteLifecycleConfig",
    "FanoutRouteLifecycleRecord",
    "FanoutRouteReason",
    "FanoutRouteState",
    "FanoutRouteTransitionResult",
    "RouteActivationGuards",
    "RouteApplyEvidence",
    "RouteEpochBinding",
    "SfuFanoutRouteLifecycle",
    "TERMINAL_ROUTE_STATES",
    "TRANSITION_TABLE",
]
