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

This module is the stable public entry point and owns the ``SideEffectLedger``
port plus the in-memory reference adapter. Record model, transition
authorization contract, validators, and the SQLite adapter live in the
``side_effect_*`` sibling modules and are re-exported here unchanged.
"""

from __future__ import annotations

import threading
import time  # noqa: F401 - kept as a module attribute for existing clock test seams
from typing import Any, Protocol

from agent.services.workflow_runtime.errors import (
    FencingTokenError,
    OptimisticConcurrencyError,
)
from agent.services.workflow_runtime.side_effect_authorization import (  # noqa: F401 - public re-export
    _MAX_OPERATION_CHARS,
    _MAX_OPERATION_RECEIPTS,
    _TRANSITION_AUTHORIZATION_RECEIPT_FIELDS,
    _TRANSITION_SIDE_EFFECT_WRITE_CLASSES,
    _WORKFLOW_TRANSITION_SIDE_EFFECT_FENCE_NAMESPACE,
    _WORKFLOW_TRANSITION_SIDE_EFFECT_INTENT_DIGEST_NAMESPACE,
    _WORKFLOW_TRANSITION_SIDE_EFFECT_OBSERVATION_DIGEST_NAMESPACE,
    _WORKFLOW_TRANSITION_SIDE_EFFECT_RECEIPT_DIGEST_NAMESPACE,
    _WORKFLOW_TRANSITION_SIDE_EFFECT_RECEIPT_NAMESPACE,
    WORKFLOW_TRANSITION_SIDE_EFFECT_AUTHORIZATION_INTENT_SCHEMA,
    WORKFLOW_TRANSITION_SIDE_EFFECT_AUTHORIZATION_RECEIPT_SCHEMA,
    WorkflowTransitionSideEffectAuthorizationCommitPort,
    WorkflowTransitionSideEffectAuthorizationIntent,
    WorkflowTransitionSideEffectAuthorizationObservation,
    WorkflowTransitionSideEffectAuthorizationReadPort,
    WorkflowTransitionSideEffectAuthorizationReceipt,
    _assert_failed_reauthorization,
    _assert_transition_authorization_current_ledger,
    _assert_transition_authorization_intent,
    _assert_transition_authorization_ledger_binding,
    _assert_transition_authorization_operation_history,
    _assert_transition_authorization_receipt,
    _assert_transition_authorization_receipt_matches_intent,
    _new_transition_authorization_receipt,
    _pristine_planned_record,
    _side_effect_record_digest,
    _transition_authorization_commit_values,
    _transition_authorization_observation,
    _transition_authorization_receipt_digest,
    _transition_authorization_relevant_receipts,
    assert_workflow_transition_side_effect_authorization_observation_digest,
    workflow_transition_side_effect_authorization_receipt_id,
    workflow_transition_side_effect_operation_fence_id,
    workflow_transition_side_effect_operation_intent_digest,
)
from agent.services.workflow_runtime.side_effect_records import (  # noqa: F401 - public re-export
    _SIDE_EFFECT_RECORD_FIELDS,
    _TRANSITIONS,
    SIDE_EFFECT_CLASSES,
    SIDE_EFFECT_LEDGER_SCHEMA,
    SIDE_EFFECT_STATUSES,
    SideEffectClaim,
    SideEffectRecord,
    _assert_side_effect_row_projection,
    _binding,
    _new_record,
    _strict_side_effect_record_mapping,
    _transition,
    side_effect_event,
)
from agent.services.workflow_runtime.side_effect_sqlite_ledger import (  # noqa: F401 - public re-export
    SQLiteSideEffectLedger,
    _sqlite_transition_authorization_receipt,
    _transition_authorization_receipt_row_values,
)
from agent.services.workflow_runtime.side_effect_validation import (  # noqa: F401 - public re-export
    _IDENTITY_RE,
    _MAX_COUNTER,
    _SHA256_RE,
    _bounded_text,
    _identity,
    _namespaced_digest,
    _nonnegative_integer,
    _positive_integer,
    _positive_timestamp,
    _runtime_identity,
    _sha256,
)
from ananta_contracts.workflow_operation import operation_id_for  # noqa: F401 - public re-export


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
