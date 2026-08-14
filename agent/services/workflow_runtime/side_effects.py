"""Hub-owned idempotency and side-effect ledger.

Tool, Native, LangGraph, and Temporal adapters use the same sequence:

1. derive ``operation_id_for(tenant, run, step, declared_operation)``;
2. ``plan`` and ``authorize`` it with the current step fencing token;
3. atomically ``claim`` before the external call;
4. ``complete``, ``fail``, or ``mark_uncertain`` using the same attempt/fence.

A crash after the external call is intentionally represented as ``uncertain`` and
never automatically re-executed. Exactly-once *decision* is provided at the hub;
the stable operation ID must also be used as downstream idempotency key whenever
the external system supports it.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from agent.services.workflow_runtime._serialization import canonical_json
from agent.services.workflow_runtime.errors import (
    FencingTokenError,
    InvalidTransitionError,
    OptimisticConcurrencyError,
)
from agent.services.workflow_runtime.events import CanonicalWorkflowEvent
from ananta_contracts.workflow_operation import operation_id_for

SIDE_EFFECT_LEDGER_SCHEMA = "ananta.side_effect_ledger.v1"
SIDE_EFFECT_CLASSES = frozenset({"read", "idempotent_write", "non_idempotent_write"})
SIDE_EFFECT_STATUSES = frozenset(
    {"planned", "authorized", "started", "completed", "failed", "uncertain", "compensated"}
)
WORKFLOW_TRANSITION_SIDE_EFFECT_AUTHORIZATION_INTENT_SCHEMA = (
    "ananta.workflow_transition_side_effect_authorization_intent.v1"
)
WORKFLOW_TRANSITION_SIDE_EFFECT_AUTHORIZATION_RECEIPT_SCHEMA = (
    "ananta.workflow_transition_side_effect_authorization_receipt.v1"
)
_WORKFLOW_TRANSITION_SIDE_EFFECT_FENCE_NAMESPACE = "workflow-transition-side-effect-operation-fence.v1"
_WORKFLOW_TRANSITION_SIDE_EFFECT_RECEIPT_NAMESPACE = "workflow-transition-side-effect-authorization-receipt.v1"
_WORKFLOW_TRANSITION_SIDE_EFFECT_INTENT_DIGEST_NAMESPACE = "workflow-transition-side-effect-operation-intent.v1"
_WORKFLOW_TRANSITION_SIDE_EFFECT_RECEIPT_DIGEST_NAMESPACE = (
    "workflow-transition-side-effect-authorization-receipt-digest.v1"
)
_WORKFLOW_TRANSITION_SIDE_EFFECT_OBSERVATION_DIGEST_NAMESPACE = (
    "workflow-transition-side-effect-authorization-observation.v1"
)
_TRANSITION_SIDE_EFFECT_WRITE_CLASSES = frozenset({"idempotent_write", "non_idempotent_write"})
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_IDENTITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")
_MAX_OPERATION_CHARS = 512
_MAX_COUNTER = 2**63 - 1
_MAX_OPERATION_RECEIPTS = 1_000
_SIDE_EFFECT_RECORD_FIELDS = frozenset(
    {
        "schema",
        "operation_id",
        "tenant_id",
        "workflow_id",
        "run_id",
        "step_id",
        "declared_operation",
        "side_effect_class",
        "status",
        "revision",
        "fencing_token",
        "attempt_id",
        "authorization_envelope_id",
        "result_ref",
        "failure_code",
        "updated_at",
    }
)
_TRANSITION_AUTHORIZATION_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "receipt_id",
        "transition_id",
        "effect_id",
        "runtime_id",
        "tenant_id",
        "workflow_id",
        "run_id",
        "step_id",
        "effect_ordinal",
        "declared_operation",
        "side_effect_class",
        "operation_id",
        "operation_payload_digest",
        "operation_intent_digest",
        "operation_fence_id",
        "authorization_envelope_id",
        "authorization_envelope_digest",
        "ownership_attempt_id",
        "ownership_fencing_token",
        "creator_claim_generation",
        "transition_request_fingerprint",
        "effect_payload_digest",
        "idempotency_key",
        "prior_status",
        "prior_revision",
        "prior_record_digest",
        "authorized_ledger_revision",
        "authorized_record_digest",
        "authorized_record",
        "planned_at",
        "authorized_at",
        "receipt_digest",
    }
)
_TRANSITIONS: dict[str, frozenset[str]] = {
    "planned": frozenset({"authorized", "failed"}),
    "authorized": frozenset({"started", "failed"}),
    "started": frozenset({"completed", "failed", "uncertain"}),
    "failed": frozenset({"authorized", "compensated"}),
    "uncertain": frozenset({"completed", "failed", "compensated"}),
    "completed": frozenset({"compensated"}),
    "compensated": frozenset(),
}


def side_effect_event(
    record: "SideEffectRecord",
    *,
    correlation_id: str,
    causation_id: str,
    actor: str = "hub",
) -> CanonicalWorkflowEvent:
    """Map a committed ledger revision to a deduplicable canonical event."""

    return CanonicalWorkflowEvent.build(
        tenant_id=record.tenant_id,
        workflow_id=record.workflow_id,
        run_id=record.run_id,
        step_id=record.step_id,
        event_type=f"workflow.side_effect.{record.status}",
        correlation_id=correlation_id,
        causation_id=causation_id,
        dedupe_key=f"side-effect:{record.operation_id}:{record.revision}",
        actor=actor,
        payload={
            "operation_id": record.operation_id,
            "declared_operation": record.declared_operation,
            "side_effect_class": record.side_effect_class,
            "fencing_token": record.fencing_token,
            "attempt_id": record.attempt_id,
            "result_ref": record.result_ref,
            "failure_code": record.failure_code,
        },
        occurred_at=record.updated_at,
        event_id=f"wfe-side-effect-{record.operation_id}-{record.revision}",
    )


@dataclass(frozen=True)
class SideEffectRecord:
    operation_id: str
    tenant_id: str
    workflow_id: str
    run_id: str
    step_id: str
    declared_operation: str
    side_effect_class: str
    status: str = "planned"
    revision: int = 1
    fencing_token: int = 0
    attempt_id: str = ""
    authorization_envelope_id: str = ""
    result_ref: str = ""
    failure_code: str = ""
    updated_at: float = 0.0
    schema: str = SIDE_EFFECT_LEDGER_SCHEMA

    def assert_valid(self) -> None:
        required = (
            self.operation_id,
            self.tenant_id,
            self.workflow_id,
            self.run_id,
            self.step_id,
            self.declared_operation,
            self.side_effect_class,
        )
        if any(not value for value in required):
            raise ValueError("side_effect_binding_required")
        expected_id = operation_id_for(
            tenant_id=self.tenant_id,
            run_id=self.run_id,
            step_id=self.step_id,
            declared_operation=self.declared_operation,
        )
        if self.operation_id != expected_id:
            raise ValueError("side_effect_operation_id_invalid")
        if self.status not in SIDE_EFFECT_STATUSES or self.revision < 1 or self.fencing_token < 0:
            raise ValueError("side_effect_state_invalid")
        if self.side_effect_class not in SIDE_EFFECT_CLASSES:
            raise ValueError("side_effect_class_invalid")
        if self.schema != SIDE_EFFECT_LEDGER_SCHEMA:
            raise ValueError("side_effect_schema_unsupported")

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "SideEffectRecord":
        record = cls(
            operation_id=str(raw.get("operation_id") or ""),
            tenant_id=str(raw.get("tenant_id") or ""),
            workflow_id=str(raw.get("workflow_id") or ""),
            run_id=str(raw.get("run_id") or ""),
            step_id=str(raw.get("step_id") or ""),
            declared_operation=str(raw.get("declared_operation") or ""),
            side_effect_class=str(raw.get("side_effect_class") or ""),
            status=str(raw.get("status") or "planned"),
            revision=int(raw.get("revision") or 0),
            fencing_token=int(raw.get("fencing_token") or 0),
            attempt_id=str(raw.get("attempt_id") or ""),
            authorization_envelope_id=str(raw.get("authorization_envelope_id") or ""),
            result_ref=str(raw.get("result_ref") or ""),
            failure_code=str(raw.get("failure_code") or ""),
            updated_at=float(raw.get("updated_at") or 0),
            schema=str(raw.get("schema") or SIDE_EFFECT_LEDGER_SCHEMA),
        )
        record.assert_valid()
        return record

    @classmethod
    def from_exact_mapping(cls, raw: Mapping[str, object]) -> "SideEffectRecord":
        """Hydrate a transition-owned row without legacy coercion/defaults."""

        safe = _strict_side_effect_record_mapping(raw)
        record = cls(**safe)
        record.assert_valid()
        return record

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "operation_id": self.operation_id,
            "tenant_id": self.tenant_id,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "step_id": self.step_id,
            "declared_operation": self.declared_operation,
            "side_effect_class": self.side_effect_class,
            "status": self.status,
            "revision": self.revision,
            "fencing_token": self.fencing_token,
            "attempt_id": self.attempt_id,
            "authorization_envelope_id": self.authorization_envelope_id,
            "result_ref": self.result_ref,
            "failure_code": self.failure_code,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class SideEffectClaim:
    record: SideEffectRecord
    acquired: bool
    reason: str


@dataclass(frozen=True)
class WorkflowTransitionSideEffectAuthorizationIntent:
    """Exact active-effect intent consumed by the atomic ledger UoW."""

    receipt_id: str
    transition_id: str
    effect_id: str
    runtime_id: str
    tenant_id: str
    workflow_id: str
    run_id: str
    step_id: str
    effect_ordinal: int
    declared_operation: str
    side_effect_class: str
    operation_id: str
    operation_payload_digest: str
    operation_intent_digest: str
    operation_fence_id: str
    authorization_envelope_id: str
    authorization_envelope_digest: str
    ownership_attempt_id: str
    ownership_fencing_token: int
    creator_claim_generation: int
    transition_request_fingerprint: str
    effect_payload_digest: str
    idempotency_key: str
    planned_at: float
    schema: str = WORKFLOW_TRANSITION_SIDE_EFFECT_AUTHORIZATION_INTENT_SCHEMA

    def __post_init__(self) -> None:
        _assert_transition_authorization_intent(self)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "receipt_id": self.receipt_id,
            "transition_id": self.transition_id,
            "effect_id": self.effect_id,
            "runtime_id": self.runtime_id,
            "tenant_id": self.tenant_id,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "step_id": self.step_id,
            "effect_ordinal": self.effect_ordinal,
            "declared_operation": self.declared_operation,
            "side_effect_class": self.side_effect_class,
            "operation_id": self.operation_id,
            "operation_payload_digest": self.operation_payload_digest,
            "operation_intent_digest": self.operation_intent_digest,
            "operation_fence_id": self.operation_fence_id,
            "authorization_envelope_id": self.authorization_envelope_id,
            "authorization_envelope_digest": self.authorization_envelope_digest,
            "ownership_attempt_id": self.ownership_attempt_id,
            "ownership_fencing_token": self.ownership_fencing_token,
            "creator_claim_generation": self.creator_claim_generation,
            "transition_request_fingerprint": self.transition_request_fingerprint,
            "effect_payload_digest": self.effect_payload_digest,
            "idempotency_key": self.idempotency_key,
            "planned_at": self.planned_at,
        }


@dataclass(frozen=True)
class WorkflowTransitionSideEffectAuthorizationReceipt:
    """Append-only proof that one exact ledger authorization was committed."""

    receipt_id: str
    transition_id: str
    effect_id: str
    runtime_id: str
    tenant_id: str
    workflow_id: str
    run_id: str
    step_id: str
    effect_ordinal: int
    declared_operation: str
    side_effect_class: str
    operation_id: str
    operation_payload_digest: str
    operation_intent_digest: str
    operation_fence_id: str
    authorization_envelope_id: str
    authorization_envelope_digest: str
    ownership_attempt_id: str
    ownership_fencing_token: int
    creator_claim_generation: int
    transition_request_fingerprint: str
    effect_payload_digest: str
    idempotency_key: str
    prior_status: str
    prior_revision: int
    prior_record_digest: str
    authorized_ledger_revision: int
    authorized_record_digest: str
    authorized_record: SideEffectRecord
    planned_at: float
    authorized_at: float
    receipt_digest: str
    schema: str = WORKFLOW_TRANSITION_SIDE_EFFECT_AUTHORIZATION_RECEIPT_SCHEMA

    def __post_init__(self) -> None:
        record = SideEffectRecord.from_exact_mapping(
            self.authorized_record.to_dict()
            if isinstance(self.authorized_record, SideEffectRecord)
            else self.authorized_record
        )
        object.__setattr__(self, "authorized_record", record)
        _assert_transition_authorization_receipt(self, authorized_record=record)

    @classmethod
    def from_mapping(
        cls,
        raw: Mapping[str, object],
    ) -> "WorkflowTransitionSideEffectAuthorizationReceipt":
        if not isinstance(raw, Mapping) or set(raw) != _TRANSITION_AUTHORIZATION_RECEIPT_FIELDS:
            raise ValueError("workflow_transition_side_effect_authorization_receipt_invalid")
        return cls(**{name: raw[name] for name in _TRANSITION_AUTHORIZATION_RECEIPT_FIELDS})

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "receipt_id": self.receipt_id,
            "transition_id": self.transition_id,
            "effect_id": self.effect_id,
            "runtime_id": self.runtime_id,
            "tenant_id": self.tenant_id,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "step_id": self.step_id,
            "effect_ordinal": self.effect_ordinal,
            "declared_operation": self.declared_operation,
            "side_effect_class": self.side_effect_class,
            "operation_id": self.operation_id,
            "operation_payload_digest": self.operation_payload_digest,
            "operation_intent_digest": self.operation_intent_digest,
            "operation_fence_id": self.operation_fence_id,
            "authorization_envelope_id": self.authorization_envelope_id,
            "authorization_envelope_digest": self.authorization_envelope_digest,
            "ownership_attempt_id": self.ownership_attempt_id,
            "ownership_fencing_token": self.ownership_fencing_token,
            "creator_claim_generation": self.creator_claim_generation,
            "transition_request_fingerprint": self.transition_request_fingerprint,
            "effect_payload_digest": self.effect_payload_digest,
            "idempotency_key": self.idempotency_key,
            "prior_status": self.prior_status,
            "prior_revision": self.prior_revision,
            "prior_record_digest": self.prior_record_digest,
            "authorized_ledger_revision": self.authorized_ledger_revision,
            "authorized_record_digest": self.authorized_record_digest,
            "authorized_record": self.authorized_record.to_dict(),
            "planned_at": self.planned_at,
            "authorized_at": self.authorized_at,
            "receipt_digest": self.receipt_digest,
        }


@dataclass(frozen=True)
class WorkflowTransitionSideEffectAuthorizationObservation:
    """One lock/transaction snapshot of receipt aliases and ledger state."""

    intent: WorkflowTransitionSideEffectAuthorizationIntent
    receipt: WorkflowTransitionSideEffectAuthorizationReceipt | None
    operation_receipts: tuple[WorkflowTransitionSideEffectAuthorizationReceipt, ...]
    ledger_record: SideEffectRecord | None
    observation_digest: str


@runtime_checkable
class WorkflowTransitionSideEffectAuthorizationReadPort(Protocol):
    def observe_transition_authorization(
        self,
        intent: WorkflowTransitionSideEffectAuthorizationIntent,
    ) -> WorkflowTransitionSideEffectAuthorizationObservation: ...


@runtime_checkable
class WorkflowTransitionSideEffectAuthorizationCommitPort(Protocol):
    def authorize_transition_effect(
        self,
        intent: WorkflowTransitionSideEffectAuthorizationIntent,
        *,
        expected_observation_digest: str,
    ) -> WorkflowTransitionSideEffectAuthorizationReceipt: ...


class SideEffectLedger(Protocol):
    def plan(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        step_id: str,
        declared_operation: str,
        side_effect_class: str,
    ) -> SideEffectRecord: ...

    def authorize(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        authorization_envelope_id: str,
    ) -> SideEffectRecord: ...

    def claim(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        attempt_id: str,
    ) -> SideEffectClaim: ...

    def complete(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        attempt_id: str,
        result_ref: str,
    ) -> SideEffectRecord: ...

    def fail(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        attempt_id: str,
        failure_code: str,
    ) -> SideEffectRecord: ...

    def mark_uncertain(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        attempt_id: str,
        failure_code: str = "outcome_unknown",
    ) -> SideEffectRecord: ...

    def reconcile_uncertain(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        failure_code: str = "owner_lost",
    ) -> SideEffectRecord: ...

    def compensate(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        result_ref: str,
    ) -> SideEffectRecord: ...

    def get(self, *, tenant_id: str, operation_id: str) -> SideEffectRecord | None: ...


class InMemorySideEffectLedger:
    def __init__(self) -> None:
        self._records: dict[str, SideEffectRecord] = {}
        self._transition_authorization_receipts: dict[str, WorkflowTransitionSideEffectAuthorizationReceipt] = {}
        self._lock = threading.RLock()

    def plan(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        step_id: str,
        declared_operation: str,
        side_effect_class: str,
    ) -> SideEffectRecord:
        record = _new_record(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            run_id=run_id,
            step_id=step_id,
            declared_operation=declared_operation,
            side_effect_class=side_effect_class,
        )
        with self._lock:
            existing = self._records.get(record.operation_id)
            if existing is not None:
                if _binding(existing) != _binding(record):
                    raise OptimisticConcurrencyError("operation_id_binding_conflict")
                return existing
            self._records[record.operation_id] = record
            return record

    def authorize(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        authorization_envelope_id: str,
    ) -> SideEffectRecord:
        if not authorization_envelope_id:
            raise ValueError("authorization_envelope_id_required")
        return self._mutate(
            operation_id,
            expected_revision=expected_revision,
            fencing_token=fencing_token,
            to_status="authorized",
            authorization_envelope_id=str(authorization_envelope_id),
        )

    def claim(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        attempt_id: str,
    ) -> SideEffectClaim:
        if not attempt_id:
            raise ValueError("attempt_id_required")
        with self._lock:
            current = self._required(operation_id)
            if current.status == "completed":
                return SideEffectClaim(current, False, "already_completed")
            if (
                current.status == "started"
                and current.fencing_token == fencing_token
                and current.attempt_id == attempt_id
            ):
                return SideEffectClaim(current, False, "already_claimed")
            updated = _transition(
                current,
                expected_revision=expected_revision,
                fencing_token=fencing_token,
                to_status="started",
                attempt_id=attempt_id,
                require_exact_fence=True,
            )
            self._records[operation_id] = updated
            return SideEffectClaim(updated, True, "acquired")

    def complete(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        attempt_id: str,
        result_ref: str,
    ) -> SideEffectRecord:
        return self._finish(
            operation_id,
            expected_revision=expected_revision,
            fencing_token=fencing_token,
            attempt_id=attempt_id,
            to_status="completed",
            result_ref=str(result_ref),
        )

    def fail(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        attempt_id: str,
        failure_code: str,
    ) -> SideEffectRecord:
        return self._finish(
            operation_id,
            expected_revision=expected_revision,
            fencing_token=fencing_token,
            attempt_id=attempt_id,
            to_status="failed",
            failure_code=str(failure_code or "operation_failed"),
        )

    def mark_uncertain(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        attempt_id: str,
        failure_code: str = "outcome_unknown",
    ) -> SideEffectRecord:
        return self._finish(
            operation_id,
            expected_revision=expected_revision,
            fencing_token=fencing_token,
            attempt_id=attempt_id,
            to_status="uncertain",
            failure_code=failure_code,
        )

    def reconcile_uncertain(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        failure_code: str = "owner_lost",
    ) -> SideEffectRecord:
        return self._mutate(
            operation_id,
            expected_revision=expected_revision,
            fencing_token=fencing_token,
            to_status="uncertain",
            failure_code=failure_code,
        )

    def compensate(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        result_ref: str,
    ) -> SideEffectRecord:
        return self._mutate(
            operation_id,
            expected_revision=expected_revision,
            fencing_token=fencing_token,
            to_status="compensated",
            result_ref=result_ref,
        )

    def get(self, *, tenant_id: str, operation_id: str) -> SideEffectRecord | None:
        with self._lock:
            record = self._records.get(str(operation_id))
            return record if record and record.tenant_id == str(tenant_id) else None

    def observe_transition_authorization(
        self,
        intent: WorkflowTransitionSideEffectAuthorizationIntent,
    ) -> WorkflowTransitionSideEffectAuthorizationObservation:
        with self._lock:
            return _transition_authorization_observation(
                intent,
                ledger_record=self._records.get(intent.operation_id),
                receipts=_transition_authorization_relevant_receipts(
                    intent,
                    self._transition_authorization_receipts.values(),
                ),
            )

    def authorize_transition_effect(
        self,
        intent: WorkflowTransitionSideEffectAuthorizationIntent,
        *,
        expected_observation_digest: str,
    ) -> WorkflowTransitionSideEffectAuthorizationReceipt:
        expected_digest = assert_workflow_transition_side_effect_authorization_observation_digest(
            expected_observation_digest
        )
        with self._lock:
            observation = _transition_authorization_observation(
                intent,
                ledger_record=self._records.get(intent.operation_id),
                receipts=_transition_authorization_relevant_receipts(
                    intent,
                    self._transition_authorization_receipts.values(),
                ),
            )
            if observation.receipt is not None:
                return observation.receipt
            if observation.observation_digest != expected_digest:
                raise OptimisticConcurrencyError("workflow_transition_side_effect_authorization_observation_conflict")
            planned, authorized, receipt = _transition_authorization_commit_values(
                intent,
                current=observation.ledger_record,
                prior_receipts=observation.operation_receipts,
            )
            self._transition_authorization_fault("after_plan", planned)
            self._transition_authorization_fault("after_authorize", authorized)
            self._transition_authorization_fault("before_receipt", receipt)
            previous_records = self._records
            previous_receipts = self._transition_authorization_receipts
            try:
                records = dict(previous_records)
                records[intent.operation_id] = authorized
                receipts = dict(previous_receipts)
                receipts[receipt.receipt_id] = receipt
                self._records = records
                self._transition_authorization_receipts = receipts
                self._transition_authorization_fault("after_publish", receipt)
            except BaseException:
                self._records = previous_records
                self._transition_authorization_receipts = previous_receipts
                raise
            return receipt

    def _transition_authorization_fault(self, stage: str, value: object) -> None:
        del stage, value

    def _required(self, operation_id: str) -> SideEffectRecord:
        record = self._records.get(str(operation_id))
        if record is None:
            raise KeyError("side_effect_operation_not_found")
        return record

    def _mutate(self, operation_id: str, **changes: Any) -> SideEffectRecord:
        with self._lock:
            current = self._required(operation_id)
            updated = _transition(current, **changes)
            self._records[operation_id] = updated
            return updated

    def _finish(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        attempt_id: str,
        to_status: str,
        result_ref: str = "",
        failure_code: str = "",
    ) -> SideEffectRecord:
        with self._lock:
            current = self._required(operation_id)
            if current.attempt_id != str(attempt_id):
                raise FencingTokenError("side_effect_attempt_mismatch")
            updated = _transition(
                current,
                expected_revision=expected_revision,
                fencing_token=fencing_token,
                to_status=to_status,
                attempt_id=attempt_id,
                result_ref=result_ref,
                failure_code=failure_code,
                require_exact_fence=True,
            )
            self._records[operation_id] = updated
            return updated


class SQLiteSideEffectLedger:
    """SQLite reference ledger; every claim/finish is a ``BEGIN IMMEDIATE`` CAS."""

    def __init__(self, database: str | Path):
        self._connection = sqlite3.connect(str(database), timeout=30, check_same_thread=False, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA busy_timeout = 30000")
        self._lock = threading.RLock()
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_side_effect_ledger (
                operation_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                status TEXT NOT NULL,
                revision INTEGER NOT NULL,
                fencing_token INTEGER NOT NULL,
                record_json TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS ix_side_effect_tenant_run ON workflow_side_effect_ledger (tenant_id, run_id)"
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_transition_side_effect_authorizations (
                receipt_id TEXT PRIMARY KEY,
                transition_id TEXT NOT NULL,
                effect_id TEXT NOT NULL UNIQUE,
                operation_id TEXT NOT NULL,
                operation_fence_id TEXT NOT NULL UNIQUE,
                tenant_id TEXT NOT NULL,
                workflow_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                runtime_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                operation_intent_digest TEXT NOT NULL,
                authorization_envelope_id TEXT NOT NULL,
                authorization_envelope_digest TEXT NOT NULL,
                ownership_attempt_id TEXT NOT NULL,
                ownership_fencing_token INTEGER NOT NULL,
                creator_claim_generation INTEGER NOT NULL,
                authorized_ledger_revision INTEGER NOT NULL,
                planned_at REAL NOT NULL,
                authorized_at REAL NOT NULL,
                receipt_digest TEXT NOT NULL,
                receipt_json TEXT NOT NULL,
                UNIQUE (operation_id, authorized_ledger_revision),
                CHECK (ownership_fencing_token > 0),
                CHECK (creator_claim_generation > 0),
                CHECK (authorized_ledger_revision > 1)
            )
            """
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS ix_transition_side_effect_auth_operation "
            "ON workflow_transition_side_effect_authorizations (operation_id)"
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS ix_transition_side_effect_auth_tenant_run "
            "ON workflow_transition_side_effect_authorizations (tenant_id, run_id)"
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS ix_transition_side_effect_auth_transition "
            "ON workflow_transition_side_effect_authorizations (transition_id)"
        )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def plan(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        step_id: str,
        declared_operation: str,
        side_effect_class: str,
    ) -> SideEffectRecord:
        record = _new_record(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            run_id=run_id,
            step_id=step_id,
            declared_operation=declared_operation,
            side_effect_class=side_effect_class,
        )
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                existing = self._read(record.operation_id)
                if existing is not None:
                    if _binding(existing) != _binding(record):
                        raise OptimisticConcurrencyError("operation_id_binding_conflict")
                    self._connection.commit()
                    return existing
                self._insert(record)
                self._connection.commit()
                return record
            except Exception:
                self._connection.rollback()
                raise

    def authorize(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        authorization_envelope_id: str,
    ) -> SideEffectRecord:
        if not authorization_envelope_id:
            raise ValueError("authorization_envelope_id_required")
        return self._mutate(
            operation_id,
            expected_revision=expected_revision,
            fencing_token=fencing_token,
            to_status="authorized",
            authorization_envelope_id=authorization_envelope_id,
        )

    def claim(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        attempt_id: str,
    ) -> SideEffectClaim:
        if not attempt_id:
            raise ValueError("attempt_id_required")
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._read_required(operation_id)
                if current.status == "completed":
                    self._connection.commit()
                    return SideEffectClaim(current, False, "already_completed")
                if (
                    current.status == "started"
                    and current.fencing_token == fencing_token
                    and current.attempt_id == attempt_id
                ):
                    self._connection.commit()
                    return SideEffectClaim(current, False, "already_claimed")
                updated = _transition(
                    current,
                    expected_revision=expected_revision,
                    fencing_token=fencing_token,
                    to_status="started",
                    attempt_id=attempt_id,
                    require_exact_fence=True,
                )
                self._update(updated, expected_previous_revision=current.revision)
                self._connection.commit()
                return SideEffectClaim(updated, True, "acquired")
            except Exception:
                self._connection.rollback()
                raise

    def complete(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        attempt_id: str,
        result_ref: str,
    ) -> SideEffectRecord:
        return self._finish(
            operation_id,
            expected_revision=expected_revision,
            fencing_token=fencing_token,
            attempt_id=attempt_id,
            to_status="completed",
            result_ref=result_ref,
        )

    def fail(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        attempt_id: str,
        failure_code: str,
    ) -> SideEffectRecord:
        return self._finish(
            operation_id,
            expected_revision=expected_revision,
            fencing_token=fencing_token,
            attempt_id=attempt_id,
            to_status="failed",
            failure_code=str(failure_code or "operation_failed"),
        )

    def mark_uncertain(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        attempt_id: str,
        failure_code: str = "outcome_unknown",
    ) -> SideEffectRecord:
        return self._finish(
            operation_id,
            expected_revision=expected_revision,
            fencing_token=fencing_token,
            attempt_id=attempt_id,
            to_status="uncertain",
            failure_code=failure_code,
        )

    def reconcile_uncertain(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        failure_code: str = "owner_lost",
    ) -> SideEffectRecord:
        return self._mutate(
            operation_id,
            expected_revision=expected_revision,
            fencing_token=fencing_token,
            to_status="uncertain",
            failure_code=failure_code,
        )

    def compensate(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        result_ref: str,
    ) -> SideEffectRecord:
        return self._mutate(
            operation_id,
            expected_revision=expected_revision,
            fencing_token=fencing_token,
            to_status="compensated",
            result_ref=result_ref,
        )

    def get(self, *, tenant_id: str, operation_id: str) -> SideEffectRecord | None:
        with self._lock:
            record = self._read(str(operation_id))
        return record if record and record.tenant_id == str(tenant_id) else None

    def observe_transition_authorization(
        self,
        intent: WorkflowTransitionSideEffectAuthorizationIntent,
    ) -> WorkflowTransitionSideEffectAuthorizationObservation:
        with self._lock:
            self._connection.execute("BEGIN")
            try:
                observation = self._read_transition_authorization_observation(intent)
                self._connection.commit()
                return observation
            except BaseException:
                self._connection.rollback()
                raise

    def authorize_transition_effect(
        self,
        intent: WorkflowTransitionSideEffectAuthorizationIntent,
        *,
        expected_observation_digest: str,
    ) -> WorkflowTransitionSideEffectAuthorizationReceipt:
        expected_digest = assert_workflow_transition_side_effect_authorization_observation_digest(
            expected_observation_digest
        )
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                observation = self._read_transition_authorization_observation(intent)
                if observation.receipt is not None:
                    self._connection.commit()
                    return observation.receipt
                if observation.observation_digest != expected_digest:
                    raise OptimisticConcurrencyError(
                        "workflow_transition_side_effect_authorization_observation_conflict"
                    )
                planned, authorized, receipt = _transition_authorization_commit_values(
                    intent,
                    current=observation.ledger_record,
                    prior_receipts=observation.operation_receipts,
                )
                if observation.ledger_record is None:
                    self._insert(planned)
                self._transition_authorization_fault("after_plan", planned)
                self._update(
                    authorized,
                    expected_previous_revision=planned.revision,
                )
                self._transition_authorization_fault("after_authorize", authorized)
                self._insert_transition_authorization_receipt(receipt)
                self._transition_authorization_fault("before_commit", receipt)
                self._connection.commit()
                return receipt
            except BaseException:
                self._connection.rollback()
                raise

    def _read_transition_authorization_observation(
        self,
        intent: WorkflowTransitionSideEffectAuthorizationIntent,
    ) -> WorkflowTransitionSideEffectAuthorizationObservation:
        ledger_record = self._read_exact(intent.operation_id)
        rows = self._connection.execute(
            """
            SELECT * FROM workflow_transition_side_effect_authorizations
            WHERE operation_id = ? OR receipt_id = ? OR effect_id = ? OR operation_fence_id = ?
            ORDER BY authorized_ledger_revision, receipt_id
            LIMIT ?
            """,
            (
                intent.operation_id,
                intent.receipt_id,
                intent.effect_id,
                intent.operation_fence_id,
                _MAX_OPERATION_RECEIPTS + 1,
            ),
        ).fetchall()
        if len(rows) > _MAX_OPERATION_RECEIPTS:
            raise OptimisticConcurrencyError("workflow_transition_side_effect_authorization_history_limit")
        receipts = tuple(_sqlite_transition_authorization_receipt(row) for row in rows)
        return _transition_authorization_observation(
            intent,
            ledger_record=ledger_record,
            receipts=receipts,
        )

    def _insert_transition_authorization_receipt(
        self,
        receipt: WorkflowTransitionSideEffectAuthorizationReceipt,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO workflow_transition_side_effect_authorizations
            (receipt_id, transition_id, effect_id, operation_id, operation_fence_id,
             tenant_id, workflow_id, run_id, runtime_id, step_id,
             operation_intent_digest, authorization_envelope_id,
             authorization_envelope_digest, ownership_attempt_id,
             ownership_fencing_token, creator_claim_generation,
             authorized_ledger_revision, planned_at, authorized_at,
             receipt_digest, receipt_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _transition_authorization_receipt_row_values(receipt),
        )

    def _transition_authorization_fault(self, stage: str, value: object) -> None:
        del stage, value

    def _finish(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        attempt_id: str,
        to_status: str,
        result_ref: str = "",
        failure_code: str = "",
    ) -> SideEffectRecord:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._read_required(operation_id)
                if current.attempt_id != attempt_id:
                    raise FencingTokenError("side_effect_attempt_mismatch")
                updated = _transition(
                    current,
                    expected_revision=expected_revision,
                    fencing_token=fencing_token,
                    to_status=to_status,
                    attempt_id=attempt_id,
                    result_ref=result_ref,
                    failure_code=failure_code,
                    require_exact_fence=True,
                )
                self._update(updated, expected_previous_revision=current.revision)
                self._connection.commit()
                return updated
            except Exception:
                self._connection.rollback()
                raise

    def _mutate(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        to_status: str,
        authorization_envelope_id: str = "",
        result_ref: str = "",
        failure_code: str = "",
    ) -> SideEffectRecord:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._read_required(operation_id)
                updated = _transition(
                    current,
                    expected_revision=expected_revision,
                    fencing_token=fencing_token,
                    to_status=to_status,
                    authorization_envelope_id=authorization_envelope_id,
                    result_ref=result_ref,
                    failure_code=failure_code,
                )
                self._update(updated, expected_previous_revision=current.revision)
                self._connection.commit()
                return updated
            except Exception:
                self._connection.rollback()
                raise

    def _read(self, operation_id: str) -> SideEffectRecord | None:
        row = self._connection.execute(
            "SELECT record_json FROM workflow_side_effect_ledger WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        return SideEffectRecord.from_mapping(json.loads(str(row["record_json"]))) if row else None

    def _read_exact(self, operation_id: str) -> SideEffectRecord | None:
        row = self._connection.execute(
            "SELECT operation_id, tenant_id, run_id, step_id, status, revision, "
            "fencing_token, record_json FROM workflow_side_effect_ledger "
            "WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            raw = json.loads(str(row["record_json"]))
            record = SideEffectRecord.from_exact_mapping(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise OptimisticConcurrencyError("workflow_transition_side_effect_ledger_record_invalid") from exc
        _assert_side_effect_row_projection(
            record,
            operation_id=row["operation_id"],
            tenant_id=row["tenant_id"],
            run_id=row["run_id"],
            step_id=row["step_id"],
            status=row["status"],
            revision=row["revision"],
            fencing_token=row["fencing_token"],
        )
        return record

    def _read_required(self, operation_id: str) -> SideEffectRecord:
        record = self._read(operation_id)
        if record is None:
            raise KeyError("side_effect_operation_not_found")
        return record

    def _insert(self, record: SideEffectRecord) -> None:
        self._connection.execute(
            """
            INSERT INTO workflow_side_effect_ledger
            (operation_id, tenant_id, run_id, step_id, status, revision, fencing_token, record_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.operation_id,
                record.tenant_id,
                record.run_id,
                record.step_id,
                record.status,
                record.revision,
                record.fencing_token,
                canonical_json(record.to_dict()),
            ),
        )

    def _update(self, record: SideEffectRecord, *, expected_previous_revision: int) -> None:
        cursor = self._connection.execute(
            """
            UPDATE workflow_side_effect_ledger
            SET status = ?, revision = ?, fencing_token = ?, record_json = ?
            WHERE operation_id = ? AND revision = ?
            """,
            (
                record.status,
                record.revision,
                record.fencing_token,
                canonical_json(record.to_dict()),
                record.operation_id,
                expected_previous_revision,
            ),
        )
        if cursor.rowcount != 1:
            raise OptimisticConcurrencyError("side_effect_compare_and_set_failed")


def _new_record(
    *,
    tenant_id: str,
    workflow_id: str,
    run_id: str,
    step_id: str,
    declared_operation: str,
    side_effect_class: str,
    timestamp: float | None = None,
) -> SideEffectRecord:
    record = SideEffectRecord(
        operation_id=operation_id_for(
            tenant_id=tenant_id,
            run_id=run_id,
            step_id=step_id,
            declared_operation=declared_operation,
        ),
        tenant_id=str(tenant_id).strip(),
        workflow_id=str(workflow_id).strip(),
        run_id=str(run_id).strip(),
        step_id=str(step_id).strip(),
        declared_operation=str(declared_operation).strip(),
        side_effect_class=str(side_effect_class).strip(),
        updated_at=float(time.time() if timestamp is None else timestamp),
    )
    record.assert_valid()
    return record


def _binding(record: SideEffectRecord) -> tuple[str, ...]:
    return (
        record.tenant_id,
        record.workflow_id,
        record.run_id,
        record.step_id,
        record.declared_operation,
        record.side_effect_class,
    )


def _transition(
    current: SideEffectRecord,
    *,
    expected_revision: int,
    fencing_token: int,
    to_status: str,
    attempt_id: str | object = "",
    authorization_envelope_id: str | object = "",
    result_ref: str | object = "",
    failure_code: str | object = "",
    require_exact_fence: bool = False,
) -> SideEffectRecord:
    if current.revision != int(expected_revision):
        raise OptimisticConcurrencyError(
            f"side_effect_revision_conflict:expected={expected_revision}:actual={current.revision}"
        )
    fence = int(fencing_token)
    if fence < current.fencing_token or (require_exact_fence and fence != current.fencing_token):
        raise FencingTokenError("side_effect_fencing_token_stale")
    if str(to_status) not in _TRANSITIONS.get(current.status, frozenset()):
        raise InvalidTransitionError(f"side_effect_transition_invalid:{current.status}:{to_status}")
    updated = replace(
        current,
        status=str(to_status),
        revision=current.revision + 1,
        fencing_token=fence,
        attempt_id=str(attempt_id or current.attempt_id),
        authorization_envelope_id=str(authorization_envelope_id or current.authorization_envelope_id),
        result_ref=str(result_ref or current.result_ref),
        failure_code=str(failure_code or ""),
        updated_at=time.time(),
    )
    updated.assert_valid()
    return updated


def workflow_transition_side_effect_operation_intent_digest(
    *,
    operation_id: str,
    tenant_id: str,
    workflow_id: str,
    run_id: str,
    step_id: str,
    declared_operation: str,
    side_effect_class: str,
    operation_payload_digest: str,
) -> str:
    payload = {
        "operation_id": operation_id,
        "tenant_id": tenant_id,
        "workflow_id": workflow_id,
        "run_id": run_id,
        "step_id": step_id,
        "declared_operation": declared_operation,
        "side_effect_class": side_effect_class,
        "operation_payload_digest": operation_payload_digest,
    }
    return _namespaced_digest(
        _WORKFLOW_TRANSITION_SIDE_EFFECT_INTENT_DIGEST_NAMESPACE,
        payload,
    )


def workflow_transition_side_effect_operation_fence_id(
    *,
    operation_id: str,
    operation_intent_digest: str,
    ownership_attempt_id: str,
    ownership_fencing_token: int,
    authorization_envelope_id: str,
    authorization_envelope_digest: str,
) -> str:
    payload = {
        "operation_id": operation_id,
        "operation_intent_digest": operation_intent_digest,
        "ownership_attempt_id": ownership_attempt_id,
        "ownership_fencing_token": ownership_fencing_token,
        "authorization_envelope_id": authorization_envelope_id,
        "authorization_envelope_digest": authorization_envelope_digest,
    }
    return "wftsf-" + _namespaced_digest(
        _WORKFLOW_TRANSITION_SIDE_EFFECT_FENCE_NAMESPACE,
        payload,
    )


def workflow_transition_side_effect_authorization_receipt_id(
    *,
    transition_id: str,
    effect_id: str,
) -> str:
    return "wftsar-" + _namespaced_digest(
        _WORKFLOW_TRANSITION_SIDE_EFFECT_RECEIPT_NAMESPACE,
        {
            "transition_id": transition_id,
            "effect_id": effect_id,
        },
    )


def _assert_transition_authorization_intent(
    intent: WorkflowTransitionSideEffectAuthorizationIntent,
) -> None:
    if intent.schema != WORKFLOW_TRANSITION_SIDE_EFFECT_AUTHORIZATION_INTENT_SCHEMA:
        raise ValueError("workflow_transition_side_effect_authorization_intent_schema_unsupported")
    for value, reason in (
        (intent.receipt_id, "receipt_id"),
        (intent.transition_id, "transition_id"),
        (intent.effect_id, "effect_id"),
        (intent.tenant_id, "tenant_id"),
        (intent.workflow_id, "workflow_id"),
        (intent.run_id, "run_id"),
        (intent.step_id, "step_id"),
        (intent.operation_id, "operation_id"),
        (intent.operation_fence_id, "operation_fence_id"),
        (intent.authorization_envelope_id, "authorization_envelope_id"),
        (intent.ownership_attempt_id, "ownership_attempt_id"),
    ):
        _identity(value, reason)
    _runtime_identity(intent.runtime_id)
    _bounded_text(intent.declared_operation, _MAX_OPERATION_CHARS, "declared_operation")
    if (
        not isinstance(intent.side_effect_class, str)
        or intent.side_effect_class not in _TRANSITION_SIDE_EFFECT_WRITE_CLASSES
    ):
        raise ValueError("workflow_transition_side_effect_authorization_class_invalid")
    _positive_integer(intent.effect_ordinal, "effect_ordinal")
    _positive_integer(intent.ownership_fencing_token, "ownership_fencing_token")
    _positive_integer(intent.creator_claim_generation, "creator_claim_generation")
    _positive_timestamp(intent.planned_at, "planned_at")
    for value, reason in (
        (intent.operation_payload_digest, "operation_payload_digest"),
        (intent.operation_intent_digest, "operation_intent_digest"),
        (intent.authorization_envelope_digest, "authorization_envelope_digest"),
        (intent.transition_request_fingerprint, "transition_request_fingerprint"),
        (intent.effect_payload_digest, "effect_payload_digest"),
    ):
        _sha256(value, reason)
    expected_operation = operation_id_for(
        tenant_id=intent.tenant_id,
        run_id=intent.run_id,
        step_id=intent.step_id,
        declared_operation=intent.declared_operation,
    )
    expected_intent_digest = workflow_transition_side_effect_operation_intent_digest(
        operation_id=intent.operation_id,
        tenant_id=intent.tenant_id,
        workflow_id=intent.workflow_id,
        run_id=intent.run_id,
        step_id=intent.step_id,
        declared_operation=intent.declared_operation,
        side_effect_class=intent.side_effect_class,
        operation_payload_digest=intent.operation_payload_digest,
    )
    expected_fence_id = workflow_transition_side_effect_operation_fence_id(
        operation_id=intent.operation_id,
        operation_intent_digest=intent.operation_intent_digest,
        ownership_attempt_id=intent.ownership_attempt_id,
        ownership_fencing_token=intent.ownership_fencing_token,
        authorization_envelope_id=intent.authorization_envelope_id,
        authorization_envelope_digest=intent.authorization_envelope_digest,
    )
    expected_receipt_id = workflow_transition_side_effect_authorization_receipt_id(
        transition_id=intent.transition_id,
        effect_id=intent.effect_id,
    )
    if (
        intent.operation_id != expected_operation
        or intent.operation_intent_digest != expected_intent_digest
        or intent.operation_fence_id != expected_fence_id
        or intent.idempotency_key != intent.operation_fence_id
        or intent.receipt_id != expected_receipt_id
    ):
        raise ValueError("workflow_transition_side_effect_authorization_intent_binding_invalid")


def _assert_transition_authorization_receipt(
    receipt: WorkflowTransitionSideEffectAuthorizationReceipt,
    *,
    authorized_record: SideEffectRecord,
) -> None:
    intent = WorkflowTransitionSideEffectAuthorizationIntent(
        receipt_id=receipt.receipt_id,
        transition_id=receipt.transition_id,
        effect_id=receipt.effect_id,
        runtime_id=receipt.runtime_id,
        tenant_id=receipt.tenant_id,
        workflow_id=receipt.workflow_id,
        run_id=receipt.run_id,
        step_id=receipt.step_id,
        effect_ordinal=receipt.effect_ordinal,
        declared_operation=receipt.declared_operation,
        side_effect_class=receipt.side_effect_class,
        operation_id=receipt.operation_id,
        operation_payload_digest=receipt.operation_payload_digest,
        operation_intent_digest=receipt.operation_intent_digest,
        operation_fence_id=receipt.operation_fence_id,
        authorization_envelope_id=receipt.authorization_envelope_id,
        authorization_envelope_digest=receipt.authorization_envelope_digest,
        ownership_attempt_id=receipt.ownership_attempt_id,
        ownership_fencing_token=receipt.ownership_fencing_token,
        creator_claim_generation=receipt.creator_claim_generation,
        transition_request_fingerprint=receipt.transition_request_fingerprint,
        effect_payload_digest=receipt.effect_payload_digest,
        idempotency_key=receipt.idempotency_key,
        planned_at=receipt.planned_at,
    )
    del intent
    if receipt.schema != WORKFLOW_TRANSITION_SIDE_EFFECT_AUTHORIZATION_RECEIPT_SCHEMA:
        raise ValueError("workflow_transition_side_effect_authorization_receipt_schema_unsupported")
    if not isinstance(receipt.prior_status, str) or receipt.prior_status not in {
        "absent",
        "planned",
        "failed",
    }:
        raise ValueError("workflow_transition_side_effect_authorization_prior_state_invalid")
    _nonnegative_integer(receipt.prior_revision, "prior_revision")
    _sha256(receipt.prior_record_digest, "prior_record_digest")
    _positive_integer(receipt.authorized_ledger_revision, "authorized_ledger_revision")
    _sha256(receipt.authorized_record_digest, "authorized_record_digest")
    _sha256(receipt.receipt_digest, "receipt_digest")
    _positive_timestamp(receipt.authorized_at, "authorized_at")
    if receipt.authorized_at != receipt.planned_at:
        raise ValueError("workflow_transition_side_effect_authorization_timestamp_invalid")
    if receipt.prior_status == "absent":
        if receipt.prior_revision != 0:
            raise ValueError("workflow_transition_side_effect_authorization_prior_state_invalid")
    elif receipt.prior_revision < 1:
        raise ValueError("workflow_transition_side_effect_authorization_prior_state_invalid")
    expected_authorized_revision = 2 if receipt.prior_status == "absent" else receipt.prior_revision + 1
    if receipt.authorized_ledger_revision != expected_authorized_revision:
        raise ValueError("workflow_transition_side_effect_authorization_revision_invalid")
    if (
        authorized_record.operation_id != receipt.operation_id
        or authorized_record.tenant_id != receipt.tenant_id
        or authorized_record.workflow_id != receipt.workflow_id
        or authorized_record.run_id != receipt.run_id
        or authorized_record.step_id != receipt.step_id
        or authorized_record.declared_operation != receipt.declared_operation
        or authorized_record.side_effect_class != receipt.side_effect_class
        or authorized_record.status != "authorized"
        or authorized_record.revision != receipt.authorized_ledger_revision
        or authorized_record.fencing_token != receipt.ownership_fencing_token
        or authorized_record.authorization_envelope_id != receipt.authorization_envelope_id
        or authorized_record.attempt_id
        or authorized_record.result_ref
        or authorized_record.failure_code
        or authorized_record.updated_at != receipt.authorized_at
    ):
        raise ValueError("workflow_transition_side_effect_authorization_record_invalid")
    if _side_effect_record_digest(authorized_record) != receipt.authorized_record_digest:
        raise ValueError("workflow_transition_side_effect_authorization_record_digest_mismatch")
    if _transition_authorization_receipt_digest(receipt) != receipt.receipt_digest:
        raise ValueError("workflow_transition_side_effect_authorization_receipt_digest_mismatch")


def _transition_authorization_observation(
    intent: WorkflowTransitionSideEffectAuthorizationIntent,
    *,
    ledger_record: SideEffectRecord | None,
    receipts: tuple[WorkflowTransitionSideEffectAuthorizationReceipt, ...],
) -> WorkflowTransitionSideEffectAuthorizationObservation:
    if not isinstance(intent, WorkflowTransitionSideEffectAuthorizationIntent):
        raise ValueError("workflow_transition_side_effect_authorization_intent_invalid")
    if len(receipts) > _MAX_OPERATION_RECEIPTS:
        raise OptimisticConcurrencyError("workflow_transition_side_effect_authorization_history_limit")
    aliases = tuple(
        receipt
        for receipt in receipts
        if receipt.receipt_id == intent.receipt_id
        or receipt.effect_id == intent.effect_id
        or receipt.operation_fence_id == intent.operation_fence_id
    )
    if aliases and any(receipt != aliases[0] for receipt in aliases[1:]):
        raise OptimisticConcurrencyError("workflow_transition_side_effect_authorization_alias_conflict")
    candidate = aliases[0] if aliases else None
    if candidate is not None:
        _assert_transition_authorization_receipt_matches_intent(candidate, intent)
    operation_receipts = tuple(
        sorted(
            (receipt for receipt in receipts if receipt.operation_id == intent.operation_id),
            key=lambda value: (value.authorized_ledger_revision, value.receipt_id),
        )
    )
    for receipt in operation_receipts:
        _assert_transition_authorization_operation_history(receipt, intent)
    for previous, current in zip(operation_receipts, operation_receipts[1:], strict=False):
        if (
            current.authorized_ledger_revision <= previous.authorized_ledger_revision
            or current.ownership_fencing_token <= previous.ownership_fencing_token
        ):
            raise OptimisticConcurrencyError("workflow_transition_side_effect_authorization_history_conflict")
    if ledger_record is not None:
        _assert_transition_authorization_ledger_binding(ledger_record, intent)
    if candidate is not None:
        _assert_transition_authorization_current_ledger(
            ledger_record,
            receipts=operation_receipts,
        )
    elif ledger_record is None:
        if operation_receipts:
            raise OptimisticConcurrencyError("workflow_transition_side_effect_authorization_ledger_missing")
    elif ledger_record.status == "planned":
        if operation_receipts or not _pristine_planned_record(ledger_record):
            raise OptimisticConcurrencyError("workflow_transition_side_effect_authorization_planned_conflict")
    elif ledger_record.status == "failed":
        _assert_failed_reauthorization(
            ledger_record,
            intent=intent,
            prior_receipts=operation_receipts,
        )
    else:
        raise OptimisticConcurrencyError("workflow_transition_side_effect_authorization_receipt_missing")
    digest = _namespaced_digest(
        _WORKFLOW_TRANSITION_SIDE_EFFECT_OBSERVATION_DIGEST_NAMESPACE,
        {
            "intent": intent.to_dict(),
            "receipt": candidate.to_dict() if candidate is not None else None,
            "operation_receipts": [value.to_dict() for value in operation_receipts],
            "ledger_record": ledger_record.to_dict() if ledger_record is not None else None,
        },
    )
    return WorkflowTransitionSideEffectAuthorizationObservation(
        intent=intent,
        receipt=candidate,
        operation_receipts=operation_receipts,
        ledger_record=ledger_record,
        observation_digest=digest,
    )


def _transition_authorization_relevant_receipts(
    intent: WorkflowTransitionSideEffectAuthorizationIntent,
    receipts: Iterable[WorkflowTransitionSideEffectAuthorizationReceipt],
) -> tuple[WorkflowTransitionSideEffectAuthorizationReceipt, ...]:
    return tuple(
        receipt
        for receipt in receipts
        if receipt.operation_id == intent.operation_id
        or receipt.receipt_id == intent.receipt_id
        or receipt.effect_id == intent.effect_id
        or receipt.operation_fence_id == intent.operation_fence_id
    )


def _transition_authorization_commit_values(
    intent: WorkflowTransitionSideEffectAuthorizationIntent,
    *,
    current: SideEffectRecord | None,
    prior_receipts: tuple[WorkflowTransitionSideEffectAuthorizationReceipt, ...],
) -> tuple[
    SideEffectRecord,
    SideEffectRecord,
    WorkflowTransitionSideEffectAuthorizationReceipt,
]:
    planned = current or _new_record(
        tenant_id=intent.tenant_id,
        workflow_id=intent.workflow_id,
        run_id=intent.run_id,
        step_id=intent.step_id,
        declared_operation=intent.declared_operation,
        side_effect_class=intent.side_effect_class,
        timestamp=intent.planned_at,
    )
    if planned.status not in {"planned", "failed"}:
        raise InvalidTransitionError("workflow_transition_side_effect_authorization_state_conflict")
    if planned.status == "planned" and not _pristine_planned_record(planned):
        raise InvalidTransitionError("workflow_transition_side_effect_authorization_planned_conflict")
    if planned.status == "failed":
        _assert_failed_reauthorization(
            planned,
            intent=intent,
            prior_receipts=prior_receipts,
        )
    authorized = replace(
        planned,
        status="authorized",
        revision=planned.revision + 1,
        fencing_token=intent.ownership_fencing_token,
        attempt_id="",
        authorization_envelope_id=intent.authorization_envelope_id,
        result_ref="",
        failure_code="",
        updated_at=intent.planned_at,
    )
    authorized.assert_valid()
    receipt = _new_transition_authorization_receipt(
        intent,
        prior=planned if current is not None else None,
        authorized=authorized,
    )
    return planned, authorized, receipt


def _new_transition_authorization_receipt(
    intent: WorkflowTransitionSideEffectAuthorizationIntent,
    *,
    prior: SideEffectRecord | None,
    authorized: SideEffectRecord,
) -> WorkflowTransitionSideEffectAuthorizationReceipt:
    values: dict[str, object] = {
        **intent.to_dict(),
        "schema": WORKFLOW_TRANSITION_SIDE_EFFECT_AUTHORIZATION_RECEIPT_SCHEMA,
        "prior_status": prior.status if prior is not None else "absent",
        "prior_revision": prior.revision if prior is not None else 0,
        "prior_record_digest": _side_effect_record_digest(prior),
        "authorized_ledger_revision": authorized.revision,
        "authorized_record_digest": _side_effect_record_digest(authorized),
        "authorized_record": authorized,
        "authorized_at": intent.planned_at,
    }
    digest_payload = {
        **values,
        "authorized_record": authorized.to_dict(),
    }
    return WorkflowTransitionSideEffectAuthorizationReceipt(
        **values,
        receipt_digest=_namespaced_digest(
            _WORKFLOW_TRANSITION_SIDE_EFFECT_RECEIPT_DIGEST_NAMESPACE,
            digest_payload,
        ),
    )


def _transition_authorization_receipt_digest(
    receipt: WorkflowTransitionSideEffectAuthorizationReceipt,
) -> str:
    payload = receipt.to_dict()
    payload.pop("receipt_digest", None)
    return _namespaced_digest(
        _WORKFLOW_TRANSITION_SIDE_EFFECT_RECEIPT_DIGEST_NAMESPACE,
        payload,
    )


def _assert_transition_authorization_receipt_matches_intent(
    receipt: WorkflowTransitionSideEffectAuthorizationReceipt,
    intent: WorkflowTransitionSideEffectAuthorizationIntent,
) -> None:
    expected = intent.to_dict()
    expected.pop("schema")
    expected.pop("creator_claim_generation")
    actual = receipt.to_dict()
    for name, value in expected.items():
        if actual.get(name) != value:
            raise OptimisticConcurrencyError("workflow_transition_side_effect_authorization_receipt_conflict")
    if receipt.creator_claim_generation > intent.creator_claim_generation:
        raise OptimisticConcurrencyError("workflow_transition_side_effect_authorization_generation_conflict")


def _assert_transition_authorization_operation_history(
    receipt: WorkflowTransitionSideEffectAuthorizationReceipt,
    intent: WorkflowTransitionSideEffectAuthorizationIntent,
) -> None:
    if (
        receipt.operation_id != intent.operation_id
        or receipt.operation_intent_digest != intent.operation_intent_digest
        or receipt.operation_payload_digest != intent.operation_payload_digest
        or receipt.tenant_id != intent.tenant_id
        or receipt.workflow_id != intent.workflow_id
        or receipt.run_id != intent.run_id
        or receipt.step_id != intent.step_id
        or receipt.declared_operation != intent.declared_operation
        or receipt.side_effect_class != intent.side_effect_class
    ):
        raise OptimisticConcurrencyError("workflow_transition_side_effect_authorization_operation_conflict")


def _assert_transition_authorization_ledger_binding(
    record: SideEffectRecord,
    intent: WorkflowTransitionSideEffectAuthorizationIntent,
) -> None:
    if (
        record.operation_id != intent.operation_id
        or record.tenant_id != intent.tenant_id
        or record.workflow_id != intent.workflow_id
        or record.run_id != intent.run_id
        or record.step_id != intent.step_id
        or record.declared_operation != intent.declared_operation
        or record.side_effect_class != intent.side_effect_class
    ):
        raise OptimisticConcurrencyError("workflow_transition_side_effect_authorization_ledger_binding_conflict")


def _assert_failed_reauthorization(
    record: SideEffectRecord,
    *,
    intent: WorkflowTransitionSideEffectAuthorizationIntent,
    prior_receipts: tuple[WorkflowTransitionSideEffectAuthorizationReceipt, ...],
) -> None:
    if not prior_receipts:
        raise OptimisticConcurrencyError("workflow_transition_side_effect_authorization_prior_receipt_missing")
    latest = prior_receipts[-1]
    if (
        record.status != "failed"
        or record.fencing_token != latest.ownership_fencing_token
        or record.authorization_envelope_id != latest.authorization_envelope_id
        or not record.attempt_id
        or record.revision <= latest.authorized_ledger_revision
        or not record.failure_code
        or intent.ownership_fencing_token <= latest.ownership_fencing_token
    ):
        raise OptimisticConcurrencyError("workflow_transition_side_effect_authorization_reauthorization_conflict")


def _assert_transition_authorization_current_ledger(
    record: SideEffectRecord | None,
    *,
    receipts: tuple[WorkflowTransitionSideEffectAuthorizationReceipt, ...],
) -> None:
    """Reject ledger regressions without binding proof to later mutable progress."""

    if record is None:
        return
    latest = receipts[-1]
    if record.revision < latest.authorized_ledger_revision:
        raise OptimisticConcurrencyError("workflow_transition_side_effect_authorization_ledger_revision_regressed")
    if record.revision == latest.authorized_ledger_revision and record != latest.authorized_record:
        raise OptimisticConcurrencyError("workflow_transition_side_effect_authorization_ledger_snapshot_conflict")


def _pristine_planned_record(record: SideEffectRecord) -> bool:
    return bool(
        record.status == "planned"
        and record.revision == 1
        and record.fencing_token == 0
        and not record.attempt_id
        and not record.authorization_envelope_id
        and not record.result_ref
        and not record.failure_code
    )


def _strict_side_effect_record_mapping(raw: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(raw, Mapping) or set(raw) != _SIDE_EFFECT_RECORD_FIELDS:
        raise ValueError("workflow_transition_side_effect_ledger_record_invalid")
    safe = dict(raw)
    for name in (
        "schema",
        "operation_id",
        "tenant_id",
        "workflow_id",
        "run_id",
        "step_id",
        "declared_operation",
        "side_effect_class",
        "status",
        "attempt_id",
        "authorization_envelope_id",
        "result_ref",
        "failure_code",
    ):
        value = safe[name]
        if not isinstance(value, str) or "\x00" in value:
            raise ValueError("workflow_transition_side_effect_ledger_record_invalid")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("workflow_transition_side_effect_ledger_record_invalid") from exc
    _positive_integer(safe["revision"], "record_revision")
    _nonnegative_integer(safe["fencing_token"], "record_fencing_token")
    _positive_timestamp(safe["updated_at"], "record_updated_at")
    return safe


def _assert_side_effect_row_projection(
    record: SideEffectRecord,
    **values: object,
) -> None:
    if any(getattr(record, name) != value for name, value in values.items()):
        raise OptimisticConcurrencyError("workflow_transition_side_effect_ledger_projection_conflict")


def _side_effect_record_digest(record: SideEffectRecord | None) -> str:
    return _namespaced_digest(
        "workflow-transition-side-effect-ledger-record.v1",
        record.to_dict() if record is not None else {"state": "absent"},
    )


def _transition_authorization_receipt_row_values(
    receipt: WorkflowTransitionSideEffectAuthorizationReceipt,
) -> tuple[object, ...]:
    return (
        receipt.receipt_id,
        receipt.transition_id,
        receipt.effect_id,
        receipt.operation_id,
        receipt.operation_fence_id,
        receipt.tenant_id,
        receipt.workflow_id,
        receipt.run_id,
        receipt.runtime_id,
        receipt.step_id,
        receipt.operation_intent_digest,
        receipt.authorization_envelope_id,
        receipt.authorization_envelope_digest,
        receipt.ownership_attempt_id,
        receipt.ownership_fencing_token,
        receipt.creator_claim_generation,
        receipt.authorized_ledger_revision,
        receipt.planned_at,
        receipt.authorized_at,
        receipt.receipt_digest,
        canonical_json(receipt.to_dict()),
    )


def _sqlite_transition_authorization_receipt(
    row: sqlite3.Row,
) -> WorkflowTransitionSideEffectAuthorizationReceipt:
    try:
        raw = json.loads(str(row["receipt_json"]))
        receipt = WorkflowTransitionSideEffectAuthorizationReceipt.from_mapping(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise OptimisticConcurrencyError("workflow_transition_side_effect_authorization_receipt_invalid") from exc
    projection = {
        "receipt_id": row["receipt_id"],
        "transition_id": row["transition_id"],
        "effect_id": row["effect_id"],
        "operation_id": row["operation_id"],
        "operation_fence_id": row["operation_fence_id"],
        "tenant_id": row["tenant_id"],
        "workflow_id": row["workflow_id"],
        "run_id": row["run_id"],
        "runtime_id": row["runtime_id"],
        "step_id": row["step_id"],
        "operation_intent_digest": row["operation_intent_digest"],
        "authorization_envelope_id": row["authorization_envelope_id"],
        "authorization_envelope_digest": row["authorization_envelope_digest"],
        "ownership_attempt_id": row["ownership_attempt_id"],
        "ownership_fencing_token": row["ownership_fencing_token"],
        "creator_claim_generation": row["creator_claim_generation"],
        "authorized_ledger_revision": row["authorized_ledger_revision"],
        "planned_at": row["planned_at"],
        "authorized_at": row["authorized_at"],
        "receipt_digest": row["receipt_digest"],
    }
    if any(getattr(receipt, name) != value for name, value in projection.items()):
        raise OptimisticConcurrencyError("workflow_transition_side_effect_authorization_receipt_projection_conflict")
    return receipt


def _identity(value: object, reason: str) -> str:
    if not isinstance(value, str) or _IDENTITY_RE.fullmatch(value) is None:
        raise ValueError(f"workflow_transition_side_effect_authorization_{reason}_invalid")
    return value


def _bounded_text(value: object, maximum: int, reason: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum or "\x00" in value:
        raise ValueError(f"workflow_transition_side_effect_authorization_{reason}_invalid")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"workflow_transition_side_effect_authorization_{reason}_invalid") from exc
    return value


def _sha256(value: object, reason: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"workflow_transition_side_effect_authorization_{reason}_invalid")
    return value


def assert_workflow_transition_side_effect_authorization_observation_digest(
    value: object,
) -> str:
    """Validate the exact digest accepted by every authorization commit adapter."""

    return _sha256(value, "expected_observation_digest")


def _runtime_identity(value: object) -> str:
    runtime_id = _identity(value, "runtime_id")
    if len(runtime_id) > 64:
        raise ValueError("workflow_transition_side_effect_authorization_runtime_id_invalid")
    return runtime_id


def _positive_integer(value: object, reason: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > _MAX_COUNTER:
        raise ValueError(f"workflow_transition_side_effect_authorization_{reason}_invalid")
    return value


def _nonnegative_integer(value: object, reason: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > _MAX_COUNTER:
        raise ValueError(f"workflow_transition_side_effect_authorization_{reason}_invalid")
    return value


def _positive_timestamp(value: object, reason: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) <= 0
    ):
        raise ValueError(f"workflow_transition_side_effect_authorization_{reason}_invalid")
    return float(value)


def _namespaced_digest(namespace: str, payload: object) -> str:
    framed = f"{namespace}\x00{canonical_json(payload)}".encode("utf-8")
    return hashlib.sha256(framed).hexdigest()
