"""In-memory and SQL persistence for the Hub workflow transition outbox."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from agent.db_models.workflow_runtime import (
    WorkflowControlBindingDB,
    WorkflowControlCommandReceiptDB,
    WorkflowTransitionEffectDB,
    WorkflowTransitionOutboxDB,
)
from agent.services.workflow_runtime._serialization import canonical_json
from agent.services.workflow_runtime.sqlalchemy_support import (
    SessionFactory,
    SQLAlchemyStoreSupport,
)
from agent.services.workflow_transition_outbox import (
    EFFECT_BINDING_FINALIZE,
    EFFECT_STATE_APPLIED,
    EFFECT_STATE_APPLYING,
    EFFECT_STATE_PLANNED,
    EFFECT_STATE_REJECTED,
    TRANSITION_STATE_APPLYING,
    TRANSITION_STATE_COMPLETED,
    TRANSITION_STATE_QUARANTINED,
    TRANSITION_STATE_READY,
    TRANSITION_STATE_REJECTED,
    WorkflowTransition,
    WorkflowTransitionEffect,
    WorkflowTransitionError,
    WorkflowTransitionPublicProjectionPort,
    WorkflowTransitionSnapshot,
    thaw_json,
    validate_transition_plan,
    workflow_transition_effect_result_digest,
    workflow_transition_effect_stage_attempt_count,
    workflow_transition_finalization_result_digest,
    workflow_transition_finalization_stage_attempt_count,
    workflow_transition_outcome_fingerprint,
    workflow_transition_request_fingerprint,
)

_RECEIPT_ACTIVE_STATES = frozenset({"pending", "dispatching"})
_ACTIVE_MARKER_TRANSITION_STATES = frozenset(
    {TRANSITION_STATE_READY, TRANSITION_STATE_APPLYING, TRANSITION_STATE_QUARANTINED}
)
_MAX_LEASE_SECONDS = 300.0
_MAX_RESULT_BYTES = 524_288


class WorkflowTransitionPersistenceError(WorkflowTransitionError):
    """Stable persistence or compare-and-set failure."""


class InMemoryWorkflowTransitionStore:
    """Thread-safe substitutable adapter for tests and explicit local use.

    Its binding and receipt records are intentionally private copies.  Slice 1
    does not compose this store with the existing process-local binding store;
    production composition will use the SQL adapter after runtime cutover.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.time,
        fault_injector: Callable[[str], None] | None = None,
        receipt_projector: WorkflowTransitionPublicProjectionPort | None = None,
    ) -> None:
        self._clock = clock
        self._fault_injector = fault_injector or (lambda _stage: None)
        self._receipt_projector = receipt_projector
        self._transitions: dict[str, WorkflowTransition] = {}
        self._effects: dict[str, tuple[WorkflowTransitionEffect, ...]] = {}
        self._command_transitions: dict[tuple[str, str, str], str] = {}
        self._receipt_transitions: dict[tuple[str, str], str] = {}
        self._bindings: dict[str, dict[str, Any]] = {}
        self._receipts: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def put_binding(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        runtime_id: str,
        runtime_revision: int,
        runtime_checkpoint_ref: str,
        last_status: Mapping[str, Any] | None = None,
        public_status: Mapping[str, Any] | None = None,
        subject_id: str = "subject",
        plan_hash: str = "plan",
        policy_version: str = "policy",
        checkpoint_id: str = "",
        workflow_request: Mapping[str, Any] | None = None,
        execution_plan: Mapping[str, Any] | None = None,
        command_receipt_id: str = "",
        dispatch_intent_id: str = "",
        command_claim: str = "",
        command_observation_pending: bool = False,
        scheduler_owner: str = "",
        scheduler_lease_expires_at: float = 0.0,
    ) -> None:
        """Seed one authoritative binding for a standalone in-memory adapter."""

        with self._lock:
            if not workflow_id or workflow_id in self._bindings:
                raise WorkflowTransitionPersistenceError("workflow_transition_binding_already_exists")
            self._bindings[workflow_id] = {
                "tenant_id": str(tenant_id),
                "workflow_id": str(workflow_id),
                "run_id": str(run_id),
                "runtime_id": str(runtime_id),
                "runtime_revision": int(runtime_revision),
                "runtime_checkpoint_ref": str(runtime_checkpoint_ref),
                "last_status": _mapping_copy(last_status or {}),
                "public_status": _mapping_copy(public_status or {}),
                "subject_id": str(subject_id),
                "plan_hash": str(plan_hash),
                "policy_version": str(policy_version),
                "checkpoint_id": str(checkpoint_id or runtime_checkpoint_ref),
                "workflow_request": _mapping_copy(workflow_request or {}),
                "execution_plan": _mapping_copy(execution_plan or {}),
                "command_receipt_id": str(command_receipt_id),
                "dispatch_intent_id": str(dispatch_intent_id),
                "command_claim": str(command_claim),
                "command_observation_pending": bool(command_observation_pending),
                "scheduler_owner": str(scheduler_owner),
                "scheduler_lease_expires_at": float(scheduler_lease_expires_at),
                "active_transition_id": "",
                "last_transition_id": "",
                "last_transition_command_id": "",
                "last_transition_request_fingerprint": "",
                "last_transition_effect_fingerprint": "",
                "last_transition_outcome_fingerprint": "",
                "revision": 1,
            }

    def put_receipt(
        self,
        *,
        receipt_id: str,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        expected_revision: int,
        checkpoint_ref: str,
        request_payload: Mapping[str, Any],
        state: str = "pending",
        dispatch_owner: str = "",
        dispatch_lease_expires_at: float = 0.0,
    ) -> None:
        """Seed one already-admitted command receipt for linkage tests."""

        if state not in _RECEIPT_ACTIVE_STATES:
            raise WorkflowTransitionPersistenceError("workflow_transition_receipt_state_invalid")
        with self._lock:
            if not receipt_id or receipt_id in self._receipts:
                raise WorkflowTransitionPersistenceError("workflow_transition_receipt_already_exists")
            self._receipts[receipt_id] = {
                "id": receipt_id,
                "tenant_id": tenant_id,
                "workflow_id": workflow_id,
                "run_id": run_id,
                "expected_revision": int(expected_revision),
                "checkpoint_ref": checkpoint_ref,
                "request_payload": _mapping_copy(request_payload),
                "state": state,
                "result_status": {},
                "rejection_reason": "",
                "dispatch_owner": str(dispatch_owner),
                "dispatch_lease_expires_at": float(dispatch_lease_expires_at),
                "request_fingerprint": "",
                "transition_id": "",
                "effect_fingerprint": "",
                "outcome_fingerprint": "",
                "dispatch_generation": 0,
                "last_heartbeat_at": 0.0,
                "revision": 1,
            }

    def binding_record(self, workflow_id: str) -> dict[str, Any] | None:
        with self._lock:
            value = self._bindings.get(str(workflow_id))
            return _mapping_copy(value) if value is not None else None

    def receipt_record(self, receipt_id: str) -> dict[str, Any] | None:
        with self._lock:
            value = self._receipts.get(str(receipt_id))
            return _mapping_copy(value) if value is not None else None

    def stage(
        self,
        transition: WorkflowTransition,
        effects: Sequence[WorkflowTransitionEffect],
        *,
        receipt_id: str = "",
    ) -> WorkflowTransitionSnapshot:
        values = validate_transition_plan(transition, effects)
        linked_receipt = _linked_receipt(transition, receipt_id)
        with self._lock:
            now = float(self._clock())
            existing = self._snapshot(transition.transition_id)
            if existing is not None:
                return _same_snapshot_or_raise(existing, transition, values)
            if transition.command_id:
                command_key = (
                    transition.tenant_id,
                    transition.workflow_id,
                    transition.command_id,
                )
                command_transition_id = self._command_transitions.get(command_key)
                if command_transition_id not in {None, transition.transition_id}:
                    raise WorkflowTransitionPersistenceError("workflow_transition_stage_conflict")
            else:
                command_key = None
            if transition.receipt_id:
                receipt_key = (transition.tenant_id, transition.receipt_id)
                receipt_transition_id = self._receipt_transitions.get(receipt_key)
                if receipt_transition_id not in {None, transition.transition_id}:
                    raise WorkflowTransitionPersistenceError("workflow_transition_stage_conflict")
            else:
                receipt_key = None
            binding = self._bindings.get(transition.workflow_id)
            _assert_memory_binding_for_stage(
                binding,
                transition=transition,
                receipt_id=linked_receipt,
                now=now,
            )
            receipt = self._receipts.get(linked_receipt) if linked_receipt else None
            _assert_memory_receipt_for_stage(
                receipt,
                transition=transition,
                now=now,
            )

            self._fault_injector("stage_after_transition")
            self._fault_injector("stage_after_effects")
            self._fault_injector("stage_before_binding_cas")

            next_binding = dict(binding or {})
            next_binding["active_transition_id"] = transition.transition_id
            next_binding["revision"] = int(next_binding.get("revision") or 0) + 1
            next_receipt = dict(receipt or {})
            if linked_receipt:
                next_receipt.update(
                    state="pending",
                    request_fingerprint=transition.request_fingerprint,
                    transition_id=transition.transition_id,
                    effect_fingerprint=transition.effect_fingerprint,
                    dispatch_owner="",
                    dispatch_lease_expires_at=0.0,
                    dispatch_generation=0,
                    last_heartbeat_at=0.0,
                    revision=int(next_receipt.get("revision") or 0) + 1,
                )
            self._fault_injector("stage_after_binding_cas")

            self._transitions[transition.transition_id] = transition
            self._effects[transition.transition_id] = values
            if command_key is not None:
                self._command_transitions[command_key] = transition.transition_id
            if receipt_key is not None:
                self._receipt_transitions[receipt_key] = transition.transition_id
            self._bindings[transition.workflow_id] = next_binding
            if linked_receipt:
                self._receipts[linked_receipt] = next_receipt
            return WorkflowTransitionSnapshot(transition, values)

    def get(self, transition_id: str) -> WorkflowTransitionSnapshot | None:
        with self._lock:
            return self._snapshot(str(transition_id))

    def get_active(self, workflow_id: str) -> WorkflowTransitionSnapshot | None:
        with self._lock:
            binding = self._bindings.get(str(workflow_id))
            transition_id = str((binding or {}).get("active_transition_id") or "")
            if not transition_id:
                return None
            transition = self._transitions.get(transition_id)
            if (
                transition is None
                or transition.workflow_id != str(workflow_id)
                or transition.state not in _ACTIVE_MARKER_TRANSITION_STATES
            ):
                raise WorkflowTransitionPersistenceError("workflow_transition_binding_marker_orphaned")
            return self._snapshot(transition_id)

    def active_transition_id(self, workflow_id: str) -> str:
        """Expose the aggregate-owned marker through a read-only fence port."""

        with self._lock:
            binding = self._bindings.get(str(workflow_id))
            transition_id = str((binding or {}).get("active_transition_id") or "")
            if transition_id and transition_id not in self._transitions:
                raise WorkflowTransitionPersistenceError("workflow_transition_binding_marker_orphaned")
            return transition_id

    def claim(
        self,
        transition_id: str,
        *,
        owner_id: str,
        lease_seconds: float,
    ) -> WorkflowTransitionSnapshot | None:
        lease = _lease_seconds(lease_seconds)
        owner = _owner_id(owner_id)
        now = float(self._clock())
        with self._lock:
            current = self._transitions.get(str(transition_id))
            if current is None:
                raise WorkflowTransitionPersistenceError("workflow_transition_not_found")
            if not _claimable(current, now=now):
                return None
            receipt = self._receipt_owned_by(current)
            _assert_receipt_lease_mirror(receipt, current)
            claimed = replace(
                current,
                state=TRANSITION_STATE_APPLYING,
                claim_owner=owner,
                claim_generation=current.claim_generation + 1,
                claim_expires_at=now + lease,
                last_heartbeat_at=now,
                attempt_count=current.attempt_count + 1,
                revision=current.revision + 1,
                updated_at=now,
            )
            if receipt is not None:
                next_receipt = dict(receipt)
                next_receipt.update(
                    state="dispatching",
                    dispatch_owner=owner,
                    dispatch_lease_expires_at=claimed.claim_expires_at,
                    dispatch_generation=claimed.claim_generation,
                    last_heartbeat_at=now,
                    revision=int(next_receipt["revision"]) + 1,
                )
                self._receipts[current.receipt_id] = next_receipt
            self._transitions[current.transition_id] = claimed
            return self._snapshot(current.transition_id)

    def claim_due(
        self,
        *,
        owner_id: str,
        lease_seconds: float,
        limit: int,
    ) -> tuple[WorkflowTransitionSnapshot, ...]:
        bounded = _limit(limit)
        now = float(self._clock())
        with self._lock:
            ids = [
                value.transition_id
                for value in sorted(
                    self._transitions.values(),
                    key=lambda item: (item.available_at, item.created_at, item.transition_id),
                )
                if _claimable(value, now=now)
            ][:bounded]
        claimed: list[WorkflowTransitionSnapshot] = []
        for transition_id in ids:
            value = self.claim(
                transition_id,
                owner_id=owner_id,
                lease_seconds=lease_seconds,
            )
            if value is not None:
                claimed.append(value)
        return tuple(claimed)

    def heartbeat(
        self,
        transition_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        lease_seconds: float,
    ) -> WorkflowTransitionSnapshot:
        lease = _lease_seconds(lease_seconds)
        now = float(self._clock())
        with self._lock:
            current = self._owned(
                transition_id,
                owner_id=owner_id,
                claim_generation=claim_generation,
                now=now,
            )
            receipt = self._receipt_owned_by(current)
            _assert_receipt_lease_mirror(receipt, current)
            updated = replace(
                current,
                claim_expires_at=now + lease,
                last_heartbeat_at=now,
                revision=current.revision + 1,
                updated_at=now,
            )
            if receipt is not None:
                next_receipt = dict(receipt)
                next_receipt.update(
                    dispatch_lease_expires_at=updated.claim_expires_at,
                    last_heartbeat_at=now,
                    revision=int(next_receipt["revision"]) + 1,
                )
                self._receipts[current.receipt_id] = next_receipt
            self._transitions[current.transition_id] = updated
            return self._snapshot(current.transition_id)  # type: ignore[return-value]

    def begin_effect(
        self,
        transition_id: str,
        effect_id: str,
        *,
        owner_id: str,
        claim_generation: int,
    ) -> WorkflowTransitionEffect:
        now = float(self._clock())
        with self._lock:
            transition = self._owned(
                transition_id,
                owner_id=owner_id,
                claim_generation=claim_generation,
                now=now,
            )
            receipt = self._receipt_owned_by(transition)
            _assert_receipt_lease_mirror(receipt, transition)
            values = list(self._effects[transition.transition_id])
            index, current = _effect(values, effect_id)
            _assert_effect_begin_order(values, effect_index=index)
            binding = self._binding_owned_by(transition)
            _assert_binding_execution_state(binding, transition)
            if any(
                candidate.effect_id != current.effect_id
                and candidate.kind != EFFECT_BINDING_FINALIZE
                and candidate.state in {EFFECT_STATE_APPLYING, EFFECT_STATE_APPLIED}
                and candidate.applied_generation == claim_generation
                for candidate in values
            ):
                raise WorkflowTransitionPersistenceError("workflow_transition_effect_claim_progress_conflict")
            if current.kind == EFFECT_BINDING_FINALIZE:
                raise WorkflowTransitionPersistenceError("workflow_transition_finalize_effect_direct_execution_denied")
            if current.state == EFFECT_STATE_APPLIED:
                return current
            if any(effect.state != EFFECT_STATE_APPLIED for effect in values[:index]):
                raise WorkflowTransitionPersistenceError("workflow_transition_effect_order_conflict")
            if current.state == EFFECT_STATE_APPLYING:
                if current.applied_generation >= claim_generation:
                    raise WorkflowTransitionPersistenceError("workflow_transition_effect_generation_conflict")
            elif current.state != EFFECT_STATE_PLANNED:
                raise WorkflowTransitionPersistenceError("workflow_transition_effect_state_conflict")
            updated = replace(
                current,
                state=EFFECT_STATE_APPLYING,
                applied_generation=claim_generation,
                revision=current.revision + 1,
                updated_at=now,
            )
            values[index] = updated
            self._effects[transition.transition_id] = tuple(values)
            return updated

    def finish_effect(
        self,
        transition_id: str,
        effect_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        result_payload: Mapping[str, Any],
        result_digest: str,
    ) -> WorkflowTransitionEffect:
        safe_result = _result_payload(result_payload, result_digest=result_digest)
        now = float(self._clock())
        with self._lock:
            transition = self._owned(
                transition_id,
                owner_id=owner_id,
                claim_generation=claim_generation,
                now=now,
            )
            receipt = self._receipt_owned_by(transition)
            _assert_receipt_lease_mirror(receipt, transition)
            values = list(self._effects[transition.transition_id])
            index, current = _effect(values, effect_id)
            if current.kind == EFFECT_BINDING_FINALIZE:
                raise WorkflowTransitionPersistenceError("workflow_transition_finalize_effect_direct_execution_denied")
            if current.state == EFFECT_STATE_APPLIED:
                if current.result_digest != result_digest or thaw_json(current.result_payload) != safe_result:
                    raise WorkflowTransitionPersistenceError("workflow_transition_effect_result_conflict")
                return current
            if current.state != EFFECT_STATE_APPLYING or current.applied_generation != claim_generation:
                raise WorkflowTransitionPersistenceError("workflow_transition_effect_generation_conflict")
            _assert_effect_stage_attempt(
                transition,
                values,
                effect_index=index,
                result_payload=safe_result,
            )
            updated = replace(
                current,
                state=EFFECT_STATE_APPLIED,
                result_payload=safe_result,
                result_digest=result_digest,
                revision=current.revision + 1,
                updated_at=now,
            )
            values[index] = updated
            self._effects[transition.transition_id] = tuple(values)
            return updated

    def release(
        self,
        transition_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        reason_code: str,
        retry_at: float,
    ) -> WorkflowTransitionSnapshot:
        reason = _reason_code(reason_code)
        retry = _retry_at(retry_at)
        now = float(self._clock())
        with self._lock:
            current = self._owned(
                transition_id,
                owner_id=owner_id,
                claim_generation=claim_generation,
                now=now,
            )
            receipt = self._receipt_owned_by(current)
            _assert_receipt_lease_mirror(receipt, current)
            binding = self._binding_owned_by(current)
            _assert_binding_execution_state(binding, current)
            updated = replace(
                current,
                state=TRANSITION_STATE_READY,
                claim_owner="",
                claim_expires_at=0.0,
                available_at=max(now, retry),
                last_heartbeat_at=now,
                last_error=reason,
                revision=current.revision + 1,
                updated_at=now,
            )
            if receipt is not None:
                next_receipt = dict(receipt)
                next_receipt.update(
                    state="pending",
                    dispatch_owner="",
                    dispatch_lease_expires_at=0.0,
                    last_heartbeat_at=now,
                    revision=int(next_receipt["revision"]) + 1,
                )
                self._receipts[current.receipt_id] = next_receipt
            self._transitions[current.transition_id] = updated
            return self._snapshot(current.transition_id)  # type: ignore[return-value]

    def yield_ready(
        self,
        transition_id: str,
        effect_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        available_at: float,
    ) -> WorkflowTransitionSnapshot:
        """Yield after one applied effect without consuming retry authority."""

        ready_at = _retry_at(available_at)
        now = float(self._clock())
        with self._lock:
            current = self._owned(
                transition_id,
                owner_id=owner_id,
                claim_generation=claim_generation,
                now=now,
            )
            receipt = self._receipt_owned_by(current)
            _assert_receipt_lease_mirror(receipt, current)
            binding = self._binding_owned_by(current)
            _assert_yield_effect(
                self._effects[current.transition_id],
                effect_id=effect_id,
                claim_generation=claim_generation,
            )
            _assert_binding_execution_state(binding, current)
            updated = replace(
                current,
                state=TRANSITION_STATE_READY,
                claim_owner="",
                claim_expires_at=0.0,
                available_at=ready_at,
                last_heartbeat_at=now,
                last_error="",
                revision=current.revision + 1,
                updated_at=now,
            )
            next_receipt = dict(receipt or {})
            if receipt is not None:
                next_receipt.update(
                    state="pending",
                    dispatch_owner="",
                    dispatch_lease_expires_at=0.0,
                    dispatch_generation=claim_generation,
                    last_heartbeat_at=now,
                    revision=int(next_receipt["revision"]) + 1,
                )
            self._fault_injector("yield_before_commit")
            self._transitions[current.transition_id] = updated
            if receipt is not None:
                self._receipts[current.receipt_id] = next_receipt
            return self._snapshot(current.transition_id)  # type: ignore[return-value]

    def quarantine(
        self,
        transition_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        reason_code: str,
    ) -> WorkflowTransitionSnapshot:
        """Atomically hold an ambiguous aggregate without inventing an outcome."""

        reason = _reason_code(reason_code)
        now = float(self._clock())
        with self._lock:
            current = self._owned(
                transition_id,
                owner_id=owner_id,
                claim_generation=claim_generation,
                now=now,
            )
            binding = self._binding_owned_by(current)
            _assert_binding_quarantine_state(binding, current)
            receipt = self._receipt_owned_by(current)
            _assert_receipt_finalize_state(receipt, current)
            quarantined = replace(
                current,
                state=TRANSITION_STATE_QUARANTINED,
                claim_owner="",
                claim_expires_at=0.0,
                last_heartbeat_at=now,
                last_error=reason,
                revision=current.revision + 1,
                updated_at=now,
            )
            next_receipt = dict(receipt or {})
            if receipt is not None:
                next_receipt.update(
                    state="pending",
                    dispatch_owner="",
                    dispatch_lease_expires_at=0.0,
                    dispatch_generation=claim_generation,
                    last_heartbeat_at=now,
                    revision=int(next_receipt["revision"]) + 1,
                )
            self._fault_injector("quarantine_before_commit")
            self._transitions[current.transition_id] = quarantined
            if receipt is not None:
                self._receipts[current.receipt_id] = next_receipt
            return self._snapshot(current.transition_id)  # type: ignore[return-value]

    def reject(
        self,
        transition_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        reason_code: str,
    ) -> WorkflowTransitionSnapshot:
        reason = _reason_code(reason_code)
        now = float(self._clock())
        with self._lock:
            current = self._owned(
                transition_id,
                owner_id=owner_id,
                claim_generation=claim_generation,
                now=now,
            )
            binding = self._binding_owned_by(current)
            _assert_binding_finalize_state(binding, current)
            receipt = self._receipt_owned_by(current)
            _assert_receipt_finalize_state(receipt, current)
            _assert_effect_rejection_safe(self._effects[current.transition_id])
            rejected_effects = tuple(
                replace(
                    effect,
                    state=EFFECT_STATE_REJECTED,
                    revision=effect.revision + 1,
                    updated_at=now,
                )
                if effect.state in {EFFECT_STATE_PLANNED, EFFECT_STATE_APPLYING}
                else effect
                for effect in self._effects[current.transition_id]
            )
            rejected = replace(
                current,
                state=TRANSITION_STATE_REJECTED,
                claim_owner="",
                claim_expires_at=0.0,
                last_heartbeat_at=now,
                last_error=reason,
                revision=current.revision + 1,
                updated_at=now,
            )
            next_binding = dict(binding)
            next_binding.update(
                active_transition_id="",
                last_transition_id=current.transition_id,
                last_transition_command_id=current.command_id,
                last_transition_request_fingerprint=current.request_fingerprint,
                last_transition_effect_fingerprint=current.effect_fingerprint,
                last_transition_outcome_fingerprint="",
            )
            next_binding["revision"] = int(next_binding["revision"]) + 1
            if current.receipt_id:
                next_binding["command_receipt_id"] = ""
            next_receipt = dict(receipt or {})
            if receipt is not None:
                next_receipt.update(
                    state="rejected",
                    rejection_reason=reason,
                    dispatch_owner="",
                    dispatch_lease_expires_at=0.0,
                    dispatch_generation=claim_generation,
                    last_heartbeat_at=now,
                    revision=int(next_receipt["revision"]) + 1,
                )
            self._fault_injector("reject_before_commit")
            self._transitions[current.transition_id] = rejected
            self._effects[current.transition_id] = rejected_effects
            self._bindings[current.workflow_id] = next_binding
            if receipt is not None:
                self._receipts[current.receipt_id] = next_receipt
            return self._snapshot(current.transition_id)  # type: ignore[return-value]

    def finalize(
        self,
        transition_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        binding_status: Mapping[str, Any],
        checkpoint_ref: str,
        finalization_proof: Mapping[str, Any],
        outcome_fingerprint: str = "",
        receipt_result: Mapping[str, Any] | None = None,
    ) -> WorkflowTransitionSnapshot:
        now = float(self._clock())
        with self._lock:
            current = self._owned(
                transition_id,
                owner_id=owner_id,
                claim_generation=claim_generation,
                now=now,
            )
            effects = self._effects[current.transition_id]
            binding = self._binding_owned_by(current)
            receipt = self._receipt_owned_by(current)
            _assert_binding_finalize_state(binding, current)
            _assert_receipt_finalize_state(receipt, current)
            public_status = _project_public_status(
                self._receipt_projector,
                transition=current,
                binding=binding,
                binding_status=binding_status,
                receipt_result=receipt_result,
            )
            status, public_status, completed_outcome, completed_effects = _finalization_values(
                current,
                effects,
                binding_status=binding_status,
                checkpoint_ref=checkpoint_ref,
                finalization_proof=finalization_proof,
                outcome_fingerprint=outcome_fingerprint,
                public_status=public_status,
                claim_generation=claim_generation,
                now=now,
            )

            next_binding = dict(binding)
            next_binding.update(
                last_status=status,
                public_status=public_status,
                runtime_revision=_status_revision(status),
                runtime_checkpoint_ref=checkpoint_ref,
                active_transition_id="",
                last_transition_id=current.transition_id,
                last_transition_command_id=current.command_id,
                last_transition_request_fingerprint=current.request_fingerprint,
                last_transition_effect_fingerprint=current.effect_fingerprint,
                last_transition_outcome_fingerprint=completed_outcome,
                revision=int(next_binding["revision"]) + 1,
            )
            if current.receipt_id:
                next_binding["command_receipt_id"] = ""
            next_receipt = dict(receipt or {})
            if receipt is not None:
                next_receipt.update(
                    state="completed",
                    result_status=public_status,
                    outcome_fingerprint=completed_outcome,
                    dispatch_owner="",
                    dispatch_lease_expires_at=0.0,
                    last_heartbeat_at=now,
                    dispatch_generation=claim_generation,
                    revision=int(next_receipt["revision"]) + 1,
                )
            completed = replace(
                current,
                state=TRANSITION_STATE_COMPLETED,
                result_status=status,
                result_checkpoint_ref=checkpoint_ref,
                outcome_fingerprint=completed_outcome,
                claim_owner="",
                claim_expires_at=0.0,
                last_heartbeat_at=now,
                revision=current.revision + 1,
                updated_at=now,
                completed_at=now,
            )

            self._fault_injector("finalize_before_binding_cas")
            self._fault_injector("finalize_after_binding_cas")
            if receipt is not None:
                self._fault_injector("finalize_after_receipt_cas")
            self._fault_injector("finalize_before_transition_cas")
            self._bindings[current.workflow_id] = next_binding
            if receipt is not None:
                self._receipts[current.receipt_id] = next_receipt
            self._effects[current.transition_id] = completed_effects
            self._transitions[current.transition_id] = completed
            return self._snapshot(current.transition_id)  # type: ignore[return-value]

    def _owned(
        self,
        transition_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        now: float,
    ) -> WorkflowTransition:
        current = self._transitions.get(str(transition_id))
        if current is None:
            raise WorkflowTransitionPersistenceError("workflow_transition_not_found")
        _assert_owned(
            current,
            owner_id=owner_id,
            claim_generation=claim_generation,
            now=now,
        )
        return current

    def _binding_owned_by(self, transition: WorkflowTransition) -> dict[str, Any]:
        binding = self._bindings.get(transition.workflow_id)
        if binding is None:
            raise WorkflowTransitionPersistenceError("workflow_transition_binding_not_found")
        if binding.get("active_transition_id") != transition.transition_id:
            raise WorkflowTransitionPersistenceError("workflow_transition_binding_cas_conflict")
        return binding

    def _receipt_owned_by(self, transition: WorkflowTransition) -> dict[str, Any] | None:
        if not transition.receipt_id:
            return None
        receipt = self._receipts.get(transition.receipt_id)
        if receipt is None:
            raise WorkflowTransitionPersistenceError("workflow_transition_receipt_not_found")
        if receipt.get("transition_id") != transition.transition_id:
            raise WorkflowTransitionPersistenceError("workflow_transition_receipt_cas_conflict")
        return receipt

    def _snapshot(self, transition_id: str) -> WorkflowTransitionSnapshot | None:
        transition = self._transitions.get(transition_id)
        if transition is None:
            return None
        return WorkflowTransitionSnapshot(
            transition,
            self._effects[transition.transition_id],
        )


class SQLAlchemyWorkflowTransitionStore(SQLAlchemyStoreSupport):
    """Transactional production adapter coupled to binding and receipt proofs."""

    def __init__(
        self,
        bind: Engine | SessionFactory,
        *,
        clock: Callable[[], float] = time.time,
        fault_injector: Callable[[str], None] | None = None,
        receipt_projector: WorkflowTransitionPublicProjectionPort | None = None,
    ) -> None:
        super().__init__(bind)
        self._clock = clock
        self._fault_injector = fault_injector or (lambda _stage: None)
        self._receipt_projector = receipt_projector

    def _locked_transition(
        self,
        session: Any,
        transition_id: str,
    ) -> WorkflowTransitionOutboxDB | None:
        statement = sa.select(WorkflowTransitionOutboxDB).where(WorkflowTransitionOutboxDB.id == str(transition_id))
        return session.execute(self._for_update(statement)).scalar_one_or_none()

    def _locked_receipt(
        self,
        session: Any,
        transition: WorkflowTransition,
    ) -> WorkflowControlCommandReceiptDB | None:
        if not transition.receipt_id:
            return None
        statement = sa.select(WorkflowControlCommandReceiptDB).where(
            WorkflowControlCommandReceiptDB.id == transition.receipt_id
        )
        return session.execute(self._for_update(statement)).scalar_one_or_none()

    def _locked_binding(
        self,
        session: Any,
        transition: WorkflowTransition,
    ) -> WorkflowControlBindingDB | None:
        statement = sa.select(WorkflowControlBindingDB).where(WorkflowControlBindingDB.id == transition.workflow_id)
        return session.execute(self._for_update(statement)).scalar_one_or_none()

    def stage(
        self,
        transition: WorkflowTransition,
        effects: Sequence[WorkflowTransitionEffect],
        *,
        receipt_id: str = "",
    ) -> WorkflowTransitionSnapshot:
        values = validate_transition_plan(transition, effects)
        linked_receipt = _linked_receipt(transition, receipt_id)
        now = float(self._clock())
        try:
            with self._transaction() as session:
                existing = session.get(WorkflowTransitionOutboxDB, transition.transition_id)
                if existing is not None:
                    return _same_snapshot_or_raise(
                        _sql_snapshot(session, existing),
                        transition,
                        values,
                    )
                binding = session.get(WorkflowControlBindingDB, transition.workflow_id)
                _assert_sql_binding_for_stage(
                    binding,
                    transition=transition,
                    receipt_id=linked_receipt,
                    now=now,
                )
                receipt = session.get(WorkflowControlCommandReceiptDB, linked_receipt) if linked_receipt else None
                _assert_sql_receipt_for_stage(
                    receipt,
                    transition=transition,
                    now=now,
                )

                transition_row = _transition_row(transition)
                effect_rows = [_effect_row(effect) for effect in values]
                session.add(transition_row)
                session.flush()
                self._fault_injector("stage_after_transition")
                session.add_all(effect_rows)
                session.flush()
                self._fault_injector("stage_after_effects")
                self._fault_injector("stage_before_binding_cas")

                binding_result = session.execute(
                    sa.update(WorkflowControlBindingDB)
                    .where(
                        WorkflowControlBindingDB.id == transition.workflow_id,
                        WorkflowControlBindingDB.tenant_id == transition.tenant_id,
                        WorkflowControlBindingDB.workflow_id == transition.workflow_id,
                        WorkflowControlBindingDB.run_id == transition.run_id,
                        WorkflowControlBindingDB.runtime_id == str(binding.runtime_id),
                        WorkflowControlBindingDB.revision == int(binding.revision),
                        WorkflowControlBindingDB.runtime_revision == transition.expected_revision,
                        WorkflowControlBindingDB.runtime_checkpoint_ref == transition.expected_checkpoint_ref,
                        WorkflowControlBindingDB.active_transition_id == "",
                        WorkflowControlBindingDB.dispatch_intent_id == "",
                        WorkflowControlBindingDB.command_claim == "",
                        WorkflowControlBindingDB.command_observation_pending.is_(False),
                        WorkflowControlBindingDB.command_receipt_id == linked_receipt,
                        sa.or_(
                            WorkflowControlBindingDB.scheduler_owner == "",
                            WorkflowControlBindingDB.scheduler_lease_expires_at <= now,
                        ),
                    )
                    .values(
                        active_transition_id=transition.transition_id,
                        revision=int(binding.revision) + 1,
                        updated_at=now,
                    )
                )
                if int(binding_result.rowcount or 0) != 1:
                    raise WorkflowTransitionPersistenceError("workflow_transition_binding_cas_conflict")
                if receipt is not None:
                    receipt_result = session.execute(
                        sa.update(WorkflowControlCommandReceiptDB)
                        .where(
                            WorkflowControlCommandReceiptDB.id == linked_receipt,
                            WorkflowControlCommandReceiptDB.tenant_id == transition.tenant_id,
                            WorkflowControlCommandReceiptDB.workflow_id == transition.workflow_id,
                            WorkflowControlCommandReceiptDB.run_id == transition.run_id,
                            WorkflowControlCommandReceiptDB.expected_revision == transition.expected_revision,
                            WorkflowControlCommandReceiptDB.checkpoint_ref == transition.expected_checkpoint_ref,
                            WorkflowControlCommandReceiptDB.revision == int(receipt.revision),
                            WorkflowControlCommandReceiptDB.transition_id == "",
                            WorkflowControlCommandReceiptDB.effect_fingerprint == "",
                            WorkflowControlCommandReceiptDB.outcome_fingerprint == "",
                            sa.or_(
                                WorkflowControlCommandReceiptDB.request_fingerprint == "",
                                WorkflowControlCommandReceiptDB.request_fingerprint == transition.request_fingerprint,
                            ),
                            WorkflowControlCommandReceiptDB.state == "pending",
                            WorkflowControlCommandReceiptDB.dispatch_owner == "",
                            WorkflowControlCommandReceiptDB.dispatch_lease_expires_at == 0.0,
                            WorkflowControlCommandReceiptDB.dispatch_generation == 0,
                            WorkflowControlCommandReceiptDB.last_heartbeat_at == 0.0,
                        )
                        .values(
                            state="pending",
                            request_fingerprint=transition.request_fingerprint,
                            transition_id=transition.transition_id,
                            effect_fingerprint=transition.effect_fingerprint,
                            dispatch_owner="",
                            dispatch_lease_expires_at=0.0,
                            dispatch_generation=0,
                            last_heartbeat_at=0.0,
                            revision=int(receipt.revision) + 1,
                            updated_at=now,
                        )
                    )
                    if int(receipt_result.rowcount or 0) != 1:
                        raise WorkflowTransitionPersistenceError("workflow_transition_receipt_cas_conflict")
                self._fault_injector("stage_after_binding_cas")
                return WorkflowTransitionSnapshot(transition, values)
        except IntegrityError as exc:
            existing = self.get(transition.transition_id)
            if existing is not None:
                return _same_snapshot_or_raise(existing, transition, values)
            raise WorkflowTransitionPersistenceError("workflow_transition_stage_conflict") from exc

    def get(self, transition_id: str) -> WorkflowTransitionSnapshot | None:
        with self._read_session() as session:
            row = session.get(WorkflowTransitionOutboxDB, str(transition_id))
            return _sql_snapshot(session, row) if row is not None else None

    def get_active(self, workflow_id: str) -> WorkflowTransitionSnapshot | None:
        with self._read_session() as session:
            binding = session.get(WorkflowControlBindingDB, str(workflow_id))
            transition_id = str((binding.active_transition_id if binding else "") or "")
            if not transition_id:
                return None
            row = session.get(WorkflowTransitionOutboxDB, transition_id)
            if (
                row is None
                or str(row.workflow_id) != str(workflow_id)
                or str(row.state) not in _ACTIVE_MARKER_TRANSITION_STATES
            ):
                raise WorkflowTransitionPersistenceError("workflow_transition_binding_marker_orphaned")
            return _sql_snapshot(session, row)

    def claim(
        self,
        transition_id: str,
        *,
        owner_id: str,
        lease_seconds: float,
    ) -> WorkflowTransitionSnapshot | None:
        owner = _owner_id(owner_id)
        lease = _lease_seconds(lease_seconds)
        now = float(self._clock())
        with self._transaction() as session:
            row = self._locked_transition(session, transition_id)
            if row is None:
                raise WorkflowTransitionPersistenceError("workflow_transition_not_found")
            if not _row_claimable(row, now=now):
                return None
            current = _transition_from_row(row)
            receipt = (
                session.execute(
                    self._for_update(
                        sa.select(WorkflowControlCommandReceiptDB).where(
                            WorkflowControlCommandReceiptDB.id == current.receipt_id
                        )
                    )
                ).scalar_one_or_none()
                if current.receipt_id
                else None
            )
            _assert_receipt_lease_mirror(receipt, current)
            expected_revision = int(row.revision)
            next_generation = current.claim_generation + 1
            next_expiry = now + lease
            result = session.execute(
                sa.update(WorkflowTransitionOutboxDB)
                .where(
                    WorkflowTransitionOutboxDB.id == row.id,
                    WorkflowTransitionOutboxDB.revision == expected_revision,
                    WorkflowTransitionOutboxDB.available_at <= now,
                    sa.or_(
                        WorkflowTransitionOutboxDB.state == TRANSITION_STATE_READY,
                        sa.and_(
                            WorkflowTransitionOutboxDB.state == TRANSITION_STATE_APPLYING,
                            WorkflowTransitionOutboxDB.claim_expires_at <= now,
                        ),
                    ),
                )
                .values(
                    state=TRANSITION_STATE_APPLYING,
                    claim_owner=owner,
                    claim_generation=next_generation,
                    claim_expires_at=next_expiry,
                    last_heartbeat_at=now,
                    attempt_count=int(row.attempt_count) + 1,
                    revision=expected_revision + 1,
                    updated_at=now,
                )
            )
            if int(result.rowcount or 0) != 1:
                return None
            if receipt is not None:
                receipt_result = session.execute(
                    sa.update(WorkflowControlCommandReceiptDB)
                    .where(
                        WorkflowControlCommandReceiptDB.id == current.receipt_id,
                        WorkflowControlCommandReceiptDB.revision == int(receipt.revision),
                        WorkflowControlCommandReceiptDB.transition_id == current.transition_id,
                        WorkflowControlCommandReceiptDB.state
                        == ("pending" if current.state == TRANSITION_STATE_READY else "dispatching"),
                        WorkflowControlCommandReceiptDB.dispatch_owner == current.claim_owner,
                        WorkflowControlCommandReceiptDB.dispatch_generation == current.claim_generation,
                        WorkflowControlCommandReceiptDB.dispatch_lease_expires_at == current.claim_expires_at,
                        WorkflowControlCommandReceiptDB.last_heartbeat_at == current.last_heartbeat_at,
                    )
                    .values(
                        state="dispatching",
                        dispatch_owner=owner,
                        dispatch_generation=next_generation,
                        dispatch_lease_expires_at=next_expiry,
                        last_heartbeat_at=now,
                        revision=int(receipt.revision) + 1,
                        updated_at=now,
                    )
                )
                if int(receipt_result.rowcount or 0) != 1:
                    raise WorkflowTransitionPersistenceError("workflow_transition_receipt_cas_conflict")
            session.flush()
            session.expire_all()
            refreshed = session.get(WorkflowTransitionOutboxDB, row.id)
            if refreshed is None:  # pragma: no cover - protected by primary key
                raise WorkflowTransitionPersistenceError("workflow_transition_not_found")
            return _sql_snapshot(session, refreshed)

    def claim_due(
        self,
        *,
        owner_id: str,
        lease_seconds: float,
        limit: int,
    ) -> tuple[WorkflowTransitionSnapshot, ...]:
        bounded = _limit(limit)
        now = float(self._clock())
        with self._read_session() as session:
            ids = (
                session.execute(
                    sa.select(WorkflowTransitionOutboxDB.id)
                    .where(
                        WorkflowTransitionOutboxDB.available_at <= now,
                        sa.or_(
                            WorkflowTransitionOutboxDB.state == TRANSITION_STATE_READY,
                            sa.and_(
                                WorkflowTransitionOutboxDB.state == TRANSITION_STATE_APPLYING,
                                WorkflowTransitionOutboxDB.claim_expires_at <= now,
                            ),
                        ),
                    )
                    .order_by(
                        WorkflowTransitionOutboxDB.available_at.asc(),
                        WorkflowTransitionOutboxDB.created_at.asc(),
                        WorkflowTransitionOutboxDB.id.asc(),
                    )
                    .limit(bounded * 4)
                )
                .scalars()
                .all()
            )
        claimed: list[WorkflowTransitionSnapshot] = []
        for transition_id in ids:
            value = self.claim(
                str(transition_id),
                owner_id=owner_id,
                lease_seconds=lease_seconds,
            )
            if value is not None:
                claimed.append(value)
                if len(claimed) >= bounded:
                    break
        return tuple(claimed)

    def heartbeat(
        self,
        transition_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        lease_seconds: float,
    ) -> WorkflowTransitionSnapshot:
        owner = _owner_id(owner_id)
        generation = _generation(claim_generation)
        lease = _lease_seconds(lease_seconds)
        now = float(self._clock())
        with self._transaction() as session:
            row = self._locked_transition(session, transition_id)
            if row is None:
                raise WorkflowTransitionPersistenceError("workflow_transition_not_found")
            transition = _transition_from_row(row)
            _assert_owned(
                transition,
                owner_id=owner,
                claim_generation=generation,
                now=now,
            )
            receipt = (
                session.execute(
                    self._for_update(
                        sa.select(WorkflowControlCommandReceiptDB).where(
                            WorkflowControlCommandReceiptDB.id == transition.receipt_id
                        )
                    )
                ).scalar_one_or_none()
                if transition.receipt_id
                else None
            )
            _assert_receipt_lease_mirror(receipt, transition)
            next_expiry = now + lease
            result = session.execute(
                sa.update(WorkflowTransitionOutboxDB)
                .where(
                    WorkflowTransitionOutboxDB.id == row.id,
                    WorkflowTransitionOutboxDB.revision == int(row.revision),
                    WorkflowTransitionOutboxDB.state == TRANSITION_STATE_APPLYING,
                    WorkflowTransitionOutboxDB.claim_owner == owner,
                    WorkflowTransitionOutboxDB.claim_generation == generation,
                    WorkflowTransitionOutboxDB.claim_expires_at > now,
                )
                .values(
                    claim_expires_at=next_expiry,
                    last_heartbeat_at=now,
                    revision=int(row.revision) + 1,
                    updated_at=now,
                )
            )
            if int(result.rowcount or 0) != 1:
                raise WorkflowTransitionPersistenceError("workflow_transition_lease_conflict")
            if receipt is not None:
                receipt_result = session.execute(
                    sa.update(WorkflowControlCommandReceiptDB)
                    .where(
                        WorkflowControlCommandReceiptDB.id == transition.receipt_id,
                        WorkflowControlCommandReceiptDB.revision == int(receipt.revision),
                        WorkflowControlCommandReceiptDB.transition_id == transition.transition_id,
                        WorkflowControlCommandReceiptDB.state == "dispatching",
                        WorkflowControlCommandReceiptDB.dispatch_owner == owner,
                        WorkflowControlCommandReceiptDB.dispatch_generation == generation,
                        WorkflowControlCommandReceiptDB.dispatch_lease_expires_at == transition.claim_expires_at,
                        WorkflowControlCommandReceiptDB.last_heartbeat_at == transition.last_heartbeat_at,
                    )
                    .values(
                        dispatch_lease_expires_at=next_expiry,
                        last_heartbeat_at=now,
                        revision=int(receipt.revision) + 1,
                        updated_at=now,
                    )
                )
                if int(receipt_result.rowcount or 0) != 1:
                    raise WorkflowTransitionPersistenceError("workflow_transition_receipt_cas_conflict")
            session.flush()
            session.expire_all()
            refreshed = session.get(WorkflowTransitionOutboxDB, row.id)
            if refreshed is None:  # pragma: no cover
                raise WorkflowTransitionPersistenceError("workflow_transition_not_found")
            return _sql_snapshot(session, refreshed)

    def begin_effect(
        self,
        transition_id: str,
        effect_id: str,
        *,
        owner_id: str,
        claim_generation: int,
    ) -> WorkflowTransitionEffect:
        now = float(self._clock())
        with self._transaction() as session:
            transition_row = self._locked_transition(session, transition_id)
            _assert_sql_owned(
                transition_row,
                owner_id=owner_id,
                claim_generation=claim_generation,
                now=now,
            )
            transition = _transition_from_row(transition_row)
            receipt = self._locked_receipt(session, transition)
            _assert_receipt_lease_mirror(receipt, transition)
            binding = self._locked_binding(session, transition)
            effect_rows = (
                session.execute(
                    self._for_update(
                        sa.select(WorkflowTransitionEffectDB)
                        .where(WorkflowTransitionEffectDB.transition_id == transition.transition_id)
                        .order_by(WorkflowTransitionEffectDB.ordinal.asc())
                    )
                )
                .scalars()
                .all()
            )
            values = tuple(_effect_from_row(effect) for effect in effect_rows)
            try:
                index, current = _effect(values, str(effect_id))
            except WorkflowTransitionPersistenceError:
                raise WorkflowTransitionPersistenceError("workflow_transition_effect_not_found")
            _assert_effect_begin_order(values, effect_index=index)
            _assert_binding_execution_state(binding, transition)
            row = effect_rows[index]
            generation = _generation(claim_generation)
            if any(
                candidate.effect_id != current.effect_id
                and candidate.kind != EFFECT_BINDING_FINALIZE
                and candidate.state in {EFFECT_STATE_APPLYING, EFFECT_STATE_APPLIED}
                and candidate.applied_generation == generation
                for candidate in values
            ):
                raise WorkflowTransitionPersistenceError("workflow_transition_effect_claim_progress_conflict")
            if current.kind == EFFECT_BINDING_FINALIZE:
                raise WorkflowTransitionPersistenceError("workflow_transition_finalize_effect_direct_execution_denied")
            if current.state == EFFECT_STATE_APPLIED:
                return current
            prior_incomplete = session.execute(
                sa.select(WorkflowTransitionEffectDB.id)
                .where(
                    WorkflowTransitionEffectDB.transition_id == str(transition_id),
                    WorkflowTransitionEffectDB.ordinal < current.ordinal,
                    WorkflowTransitionEffectDB.state != EFFECT_STATE_APPLIED,
                )
                .limit(1)
            ).scalar_one_or_none()
            if prior_incomplete is not None:
                raise WorkflowTransitionPersistenceError("workflow_transition_effect_order_conflict")
            if current.state == EFFECT_STATE_APPLYING:
                if current.applied_generation >= generation:
                    raise WorkflowTransitionPersistenceError("workflow_transition_effect_generation_conflict")
            elif current.state != EFFECT_STATE_PLANNED:
                raise WorkflowTransitionPersistenceError("workflow_transition_effect_state_conflict")
            result = session.execute(
                sa.update(WorkflowTransitionEffectDB)
                .where(
                    WorkflowTransitionEffectDB.id == row.id,
                    WorkflowTransitionEffectDB.transition_id == str(transition_id),
                    WorkflowTransitionEffectDB.revision == int(row.revision),
                    WorkflowTransitionEffectDB.state == current.state,
                    WorkflowTransitionEffectDB.applied_generation == current.applied_generation,
                )
                .values(
                    state=EFFECT_STATE_APPLYING,
                    applied_generation=generation,
                    revision=int(row.revision) + 1,
                    updated_at=now,
                )
            )
            if int(result.rowcount or 0) != 1:
                raise WorkflowTransitionPersistenceError("workflow_transition_effect_generation_conflict")
            session.flush()
            session.expire_all()
            refreshed = session.get(WorkflowTransitionEffectDB, row.id)
            if refreshed is None:  # pragma: no cover
                raise WorkflowTransitionPersistenceError("workflow_transition_effect_not_found")
            return _effect_from_row(refreshed)

    def finish_effect(
        self,
        transition_id: str,
        effect_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        result_payload: Mapping[str, Any],
        result_digest: str,
    ) -> WorkflowTransitionEffect:
        safe_result = _result_payload(result_payload, result_digest=result_digest)
        generation = _generation(claim_generation)
        now = float(self._clock())
        with self._transaction() as session:
            transition_row = self._locked_transition(session, transition_id)
            _assert_sql_owned(
                transition_row,
                owner_id=owner_id,
                claim_generation=generation,
                now=now,
            )
            transition = _transition_from_row(transition_row)
            receipt = self._locked_receipt(session, transition)
            _assert_receipt_lease_mirror(receipt, transition)
            effect_rows = (
                session.execute(
                    self._for_update(
                        sa.select(WorkflowTransitionEffectDB)
                        .where(WorkflowTransitionEffectDB.transition_id == transition.transition_id)
                        .order_by(WorkflowTransitionEffectDB.ordinal.asc())
                    )
                )
                .scalars()
                .all()
            )
            values = tuple(_effect_from_row(effect) for effect in effect_rows)
            try:
                index, current = _effect(values, str(effect_id))
            except WorkflowTransitionPersistenceError:
                raise WorkflowTransitionPersistenceError("workflow_transition_effect_not_found")
            row = effect_rows[index]
            if current.kind == EFFECT_BINDING_FINALIZE:
                raise WorkflowTransitionPersistenceError("workflow_transition_finalize_effect_direct_execution_denied")
            if current.state == EFFECT_STATE_APPLIED:
                if current.result_digest != result_digest or thaw_json(current.result_payload) != safe_result:
                    raise WorkflowTransitionPersistenceError("workflow_transition_effect_result_conflict")
                return current
            if current.state != EFFECT_STATE_APPLYING or current.applied_generation != generation:
                raise WorkflowTransitionPersistenceError("workflow_transition_effect_generation_conflict")
            _assert_effect_stage_attempt(
                transition,
                values,
                effect_index=index,
                result_payload=safe_result,
            )
            result = session.execute(
                sa.update(WorkflowTransitionEffectDB)
                .where(
                    WorkflowTransitionEffectDB.id == row.id,
                    WorkflowTransitionEffectDB.transition_id == str(transition_id),
                    WorkflowTransitionEffectDB.revision == int(row.revision),
                    WorkflowTransitionEffectDB.state == EFFECT_STATE_APPLYING,
                    WorkflowTransitionEffectDB.applied_generation == generation,
                )
                .values(
                    state=EFFECT_STATE_APPLIED,
                    result_payload=safe_result,
                    result_digest=result_digest,
                    revision=int(row.revision) + 1,
                    updated_at=now,
                )
            )
            if int(result.rowcount or 0) != 1:
                raise WorkflowTransitionPersistenceError("workflow_transition_effect_generation_conflict")
            session.flush()
            session.expire_all()
            refreshed = session.get(WorkflowTransitionEffectDB, row.id)
            if refreshed is None:  # pragma: no cover
                raise WorkflowTransitionPersistenceError("workflow_transition_effect_not_found")
            return _effect_from_row(refreshed)

    def release(
        self,
        transition_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        reason_code: str,
        retry_at: float,
    ) -> WorkflowTransitionSnapshot:
        reason = _reason_code(reason_code)
        retry = _retry_at(retry_at)
        owner = _owner_id(owner_id)
        generation = _generation(claim_generation)
        now = float(self._clock())
        with self._transaction() as session:
            row = self._locked_transition(session, transition_id)
            if row is None:
                raise WorkflowTransitionPersistenceError("workflow_transition_not_found")
            transition = _transition_from_row(row)
            _assert_owned(
                transition,
                owner_id=owner,
                claim_generation=generation,
                now=now,
            )
            receipt = (
                session.execute(
                    self._for_update(
                        sa.select(WorkflowControlCommandReceiptDB).where(
                            WorkflowControlCommandReceiptDB.id == transition.receipt_id
                        )
                    )
                ).scalar_one_or_none()
                if transition.receipt_id
                else None
            )
            _assert_receipt_lease_mirror(receipt, transition)
            binding = self._locked_binding(session, transition)
            _assert_binding_execution_state(binding, transition)
            result = session.execute(
                sa.update(WorkflowTransitionOutboxDB)
                .where(
                    WorkflowTransitionOutboxDB.id == row.id,
                    WorkflowTransitionOutboxDB.revision == int(row.revision),
                    WorkflowTransitionOutboxDB.state == TRANSITION_STATE_APPLYING,
                    WorkflowTransitionOutboxDB.claim_owner == owner,
                    WorkflowTransitionOutboxDB.claim_generation == generation,
                    WorkflowTransitionOutboxDB.claim_expires_at > now,
                )
                .values(
                    state=TRANSITION_STATE_READY,
                    claim_owner="",
                    claim_expires_at=0.0,
                    available_at=max(now, retry),
                    last_heartbeat_at=now,
                    last_error=reason,
                    revision=int(row.revision) + 1,
                    updated_at=now,
                )
            )
            if int(result.rowcount or 0) != 1:
                raise WorkflowTransitionPersistenceError("workflow_transition_lease_conflict")
            if receipt is not None:
                receipt_result = session.execute(
                    sa.update(WorkflowControlCommandReceiptDB)
                    .where(
                        WorkflowControlCommandReceiptDB.id == transition.receipt_id,
                        WorkflowControlCommandReceiptDB.revision == int(receipt.revision),
                        WorkflowControlCommandReceiptDB.transition_id == transition.transition_id,
                        WorkflowControlCommandReceiptDB.state == "dispatching",
                        WorkflowControlCommandReceiptDB.dispatch_owner == owner,
                        WorkflowControlCommandReceiptDB.dispatch_generation == generation,
                        WorkflowControlCommandReceiptDB.dispatch_lease_expires_at == transition.claim_expires_at,
                        WorkflowControlCommandReceiptDB.last_heartbeat_at == transition.last_heartbeat_at,
                    )
                    .values(
                        state="pending",
                        dispatch_owner="",
                        dispatch_lease_expires_at=0.0,
                        last_heartbeat_at=now,
                        revision=int(receipt.revision) + 1,
                        updated_at=now,
                    )
                )
                if int(receipt_result.rowcount or 0) != 1:
                    raise WorkflowTransitionPersistenceError("workflow_transition_receipt_cas_conflict")
            session.flush()
            session.expire_all()
            refreshed = session.get(WorkflowTransitionOutboxDB, row.id)
            if refreshed is None:  # pragma: no cover
                raise WorkflowTransitionPersistenceError("workflow_transition_not_found")
            return _sql_snapshot(session, refreshed)

    def yield_ready(
        self,
        transition_id: str,
        effect_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        available_at: float,
    ) -> WorkflowTransitionSnapshot:
        """Yield after one current-generation effect proof in the same UoW."""

        ready_at = _retry_at(available_at)
        owner = _owner_id(owner_id)
        generation = _generation(claim_generation)
        now = float(self._clock())
        with self._transaction() as session:
            row = self._locked_transition(session, transition_id)
            _assert_sql_owned(
                row,
                owner_id=owner,
                claim_generation=generation,
                now=now,
            )
            transition = _transition_from_row(row)
            receipt = self._locked_receipt(session, transition)
            _assert_receipt_lease_mirror(receipt, transition)
            binding = self._locked_binding(session, transition)
            effect_rows = (
                session.execute(
                    self._for_update(
                        sa.select(WorkflowTransitionEffectDB)
                        .where(WorkflowTransitionEffectDB.transition_id == transition.transition_id)
                        .order_by(WorkflowTransitionEffectDB.ordinal.asc())
                    )
                )
                .scalars()
                .all()
            )
            _assert_yield_effect(
                tuple(_effect_from_row(effect) for effect in effect_rows),
                effect_id=effect_id,
                claim_generation=generation,
            )
            _assert_binding_execution_state(binding, transition)

            transition_result = session.execute(
                sa.update(WorkflowTransitionOutboxDB)
                .where(
                    WorkflowTransitionOutboxDB.id == transition.transition_id,
                    WorkflowTransitionOutboxDB.revision == transition.revision,
                    WorkflowTransitionOutboxDB.state == TRANSITION_STATE_APPLYING,
                    WorkflowTransitionOutboxDB.claim_owner == owner,
                    WorkflowTransitionOutboxDB.claim_generation == generation,
                    WorkflowTransitionOutboxDB.claim_expires_at > now,
                )
                .values(
                    state=TRANSITION_STATE_READY,
                    claim_owner="",
                    claim_expires_at=0.0,
                    available_at=ready_at,
                    last_heartbeat_at=now,
                    last_error="",
                    revision=transition.revision + 1,
                    updated_at=now,
                )
            )
            if int(transition_result.rowcount or 0) != 1:
                raise WorkflowTransitionPersistenceError("workflow_transition_lease_conflict")
            if receipt is not None:
                receipt_result = session.execute(
                    sa.update(WorkflowControlCommandReceiptDB)
                    .where(
                        WorkflowControlCommandReceiptDB.id == transition.receipt_id,
                        WorkflowControlCommandReceiptDB.revision == int(receipt.revision),
                        WorkflowControlCommandReceiptDB.transition_id == transition.transition_id,
                        WorkflowControlCommandReceiptDB.request_fingerprint == transition.request_fingerprint,
                        WorkflowControlCommandReceiptDB.effect_fingerprint == transition.effect_fingerprint,
                        WorkflowControlCommandReceiptDB.state == "dispatching",
                        WorkflowControlCommandReceiptDB.dispatch_owner == owner,
                        WorkflowControlCommandReceiptDB.dispatch_generation == generation,
                        WorkflowControlCommandReceiptDB.dispatch_lease_expires_at == transition.claim_expires_at,
                        WorkflowControlCommandReceiptDB.last_heartbeat_at == transition.last_heartbeat_at,
                    )
                    .values(
                        state="pending",
                        dispatch_owner="",
                        dispatch_lease_expires_at=0.0,
                        dispatch_generation=generation,
                        last_heartbeat_at=now,
                        revision=int(receipt.revision) + 1,
                        updated_at=now,
                    )
                )
                if int(receipt_result.rowcount or 0) != 1:
                    raise WorkflowTransitionPersistenceError("workflow_transition_receipt_cas_conflict")
            self._fault_injector("yield_before_commit")
            session.flush()
            session.expire_all()
            refreshed = session.get(WorkflowTransitionOutboxDB, transition.transition_id)
            if refreshed is None:  # pragma: no cover
                raise WorkflowTransitionPersistenceError("workflow_transition_not_found")
            return _sql_snapshot(session, refreshed)

    def quarantine(
        self,
        transition_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        reason_code: str,
    ) -> WorkflowTransitionSnapshot:
        """Atomically hold an ambiguous aggregate without changing its effects."""

        reason = _reason_code(reason_code)
        owner = _owner_id(owner_id)
        generation = _generation(claim_generation)
        now = float(self._clock())
        with self._transaction() as session:
            transition_row = self._locked_transition(session, transition_id)
            _assert_sql_owned(
                transition_row,
                owner_id=owner,
                claim_generation=generation,
                now=now,
            )
            transition = _transition_from_row(transition_row)
            binding = session.execute(
                self._for_update(
                    sa.select(WorkflowControlBindingDB).where(WorkflowControlBindingDB.id == transition.workflow_id)
                )
            ).scalar_one_or_none()
            _assert_binding_quarantine_state(binding, transition)
            receipt = self._locked_receipt(session, transition)
            _assert_receipt_finalize_state(receipt, transition)

            if receipt is not None:
                receipt_result = session.execute(
                    sa.update(WorkflowControlCommandReceiptDB)
                    .where(
                        WorkflowControlCommandReceiptDB.id == transition.receipt_id,
                        WorkflowControlCommandReceiptDB.revision == int(receipt.revision),
                        WorkflowControlCommandReceiptDB.transition_id == transition.transition_id,
                        WorkflowControlCommandReceiptDB.request_fingerprint == transition.request_fingerprint,
                        WorkflowControlCommandReceiptDB.effect_fingerprint == transition.effect_fingerprint,
                        WorkflowControlCommandReceiptDB.state == "dispatching",
                        WorkflowControlCommandReceiptDB.dispatch_owner == owner,
                        WorkflowControlCommandReceiptDB.dispatch_generation == generation,
                        WorkflowControlCommandReceiptDB.dispatch_lease_expires_at == transition.claim_expires_at,
                        WorkflowControlCommandReceiptDB.last_heartbeat_at == transition.last_heartbeat_at,
                    )
                    .values(
                        state="pending",
                        dispatch_owner="",
                        dispatch_lease_expires_at=0.0,
                        dispatch_generation=generation,
                        last_heartbeat_at=now,
                        revision=int(receipt.revision) + 1,
                        updated_at=now,
                    )
                )
                if int(receipt_result.rowcount or 0) != 1:
                    raise WorkflowTransitionPersistenceError("workflow_transition_receipt_cas_conflict")
                self._fault_injector("quarantine_after_receipt_cas")

            transition_result = session.execute(
                sa.update(WorkflowTransitionOutboxDB)
                .where(
                    WorkflowTransitionOutboxDB.id == transition.transition_id,
                    WorkflowTransitionOutboxDB.revision == transition.revision,
                    WorkflowTransitionOutboxDB.state == TRANSITION_STATE_APPLYING,
                    WorkflowTransitionOutboxDB.claim_owner == owner,
                    WorkflowTransitionOutboxDB.claim_generation == generation,
                    WorkflowTransitionOutboxDB.claim_expires_at > now,
                )
                .values(
                    state=TRANSITION_STATE_QUARANTINED,
                    claim_owner="",
                    claim_expires_at=0.0,
                    last_heartbeat_at=now,
                    last_error=reason,
                    revision=transition.revision + 1,
                    updated_at=now,
                )
            )
            if int(transition_result.rowcount or 0) != 1:
                raise WorkflowTransitionPersistenceError("workflow_transition_lease_conflict")
            self._fault_injector("quarantine_before_commit")
            session.flush()
            session.expire_all()
            refreshed = session.get(WorkflowTransitionOutboxDB, transition.transition_id)
            if refreshed is None:  # pragma: no cover
                raise WorkflowTransitionPersistenceError("workflow_transition_not_found")
            return _sql_snapshot(session, refreshed)

    def reject(
        self,
        transition_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        reason_code: str,
    ) -> WorkflowTransitionSnapshot:
        reason = _reason_code(reason_code)
        owner = _owner_id(owner_id)
        generation = _generation(claim_generation)
        now = float(self._clock())
        with self._transaction() as session:
            transition_row = self._locked_transition(session, transition_id)
            _assert_sql_owned(
                transition_row,
                owner_id=owner,
                claim_generation=generation,
                now=now,
            )
            transition = _transition_from_row(transition_row)
            binding = session.execute(
                self._for_update(
                    sa.select(WorkflowControlBindingDB).where(WorkflowControlBindingDB.id == transition.workflow_id)
                )
            ).scalar_one_or_none()
            _assert_binding_finalize_state(binding, transition)
            receipt = (
                session.execute(
                    self._for_update(
                        sa.select(WorkflowControlCommandReceiptDB).where(
                            WorkflowControlCommandReceiptDB.id == transition.receipt_id
                        )
                    )
                ).scalar_one_or_none()
                if transition.receipt_id
                else None
            )
            _assert_receipt_finalize_state(receipt, transition)

            effect_rows = (
                session.execute(
                    self._for_update(
                        sa.select(WorkflowTransitionEffectDB)
                        .where(WorkflowTransitionEffectDB.transition_id == transition.transition_id)
                        .order_by(WorkflowTransitionEffectDB.ordinal.asc())
                    )
                )
                .scalars()
                .all()
            )
            _assert_effect_rejection_safe(tuple(_effect_from_row(row) for row in effect_rows))
            for effect_row in effect_rows:
                if effect_row.state not in {
                    EFFECT_STATE_PLANNED,
                    EFFECT_STATE_APPLYING,
                }:
                    continue
                effect_result = session.execute(
                    sa.update(WorkflowTransitionEffectDB)
                    .where(
                        WorkflowTransitionEffectDB.id == effect_row.id,
                        WorkflowTransitionEffectDB.transition_id == transition.transition_id,
                        WorkflowTransitionEffectDB.revision == int(effect_row.revision),
                        WorkflowTransitionEffectDB.state == effect_row.state,
                        WorkflowTransitionEffectDB.applied_generation == int(effect_row.applied_generation),
                    )
                    .values(
                        state=EFFECT_STATE_REJECTED,
                        revision=int(effect_row.revision) + 1,
                        updated_at=now,
                    )
                )
                if int(effect_result.rowcount or 0) != 1:
                    raise WorkflowTransitionPersistenceError("workflow_transition_effect_cas_conflict")

            binding_result = session.execute(
                sa.update(WorkflowControlBindingDB)
                .where(
                    WorkflowControlBindingDB.id == transition.workflow_id,
                    WorkflowControlBindingDB.revision == int(binding.revision),
                    WorkflowControlBindingDB.active_transition_id == transition.transition_id,
                    WorkflowControlBindingDB.runtime_revision == transition.expected_revision,
                    WorkflowControlBindingDB.runtime_checkpoint_ref == transition.expected_checkpoint_ref,
                    WorkflowControlBindingDB.command_receipt_id == transition.receipt_id,
                )
                .values(
                    active_transition_id="",
                    command_receipt_id="" if transition.receipt_id else binding.command_receipt_id,
                    last_transition_id=transition.transition_id,
                    last_transition_command_id=transition.command_id,
                    last_transition_request_fingerprint=transition.request_fingerprint,
                    last_transition_effect_fingerprint=transition.effect_fingerprint,
                    last_transition_outcome_fingerprint="",
                    revision=int(binding.revision) + 1,
                    updated_at=now,
                )
            )
            if int(binding_result.rowcount or 0) != 1:
                raise WorkflowTransitionPersistenceError("workflow_transition_binding_cas_conflict")
            if receipt is not None:
                receipt_result = session.execute(
                    sa.update(WorkflowControlCommandReceiptDB)
                    .where(
                        WorkflowControlCommandReceiptDB.id == transition.receipt_id,
                        WorkflowControlCommandReceiptDB.revision == int(receipt.revision),
                        WorkflowControlCommandReceiptDB.transition_id == transition.transition_id,
                        WorkflowControlCommandReceiptDB.request_fingerprint == transition.request_fingerprint,
                        WorkflowControlCommandReceiptDB.effect_fingerprint == transition.effect_fingerprint,
                        WorkflowControlCommandReceiptDB.state == "dispatching",
                        WorkflowControlCommandReceiptDB.dispatch_owner == owner,
                        WorkflowControlCommandReceiptDB.dispatch_generation == generation,
                        WorkflowControlCommandReceiptDB.dispatch_lease_expires_at == transition.claim_expires_at,
                        WorkflowControlCommandReceiptDB.last_heartbeat_at == transition.last_heartbeat_at,
                    )
                    .values(
                        state="rejected",
                        rejection_reason=reason,
                        dispatch_owner="",
                        dispatch_lease_expires_at=0.0,
                        dispatch_generation=generation,
                        last_heartbeat_at=now,
                        revision=int(receipt.revision) + 1,
                        updated_at=now,
                    )
                )
                if int(receipt_result.rowcount or 0) != 1:
                    raise WorkflowTransitionPersistenceError("workflow_transition_receipt_cas_conflict")

            transition_result = session.execute(
                sa.update(WorkflowTransitionOutboxDB)
                .where(
                    WorkflowTransitionOutboxDB.id == transition.transition_id,
                    WorkflowTransitionOutboxDB.revision == transition.revision,
                    WorkflowTransitionOutboxDB.state == TRANSITION_STATE_APPLYING,
                    WorkflowTransitionOutboxDB.claim_owner == owner,
                    WorkflowTransitionOutboxDB.claim_generation == generation,
                    WorkflowTransitionOutboxDB.claim_expires_at > now,
                )
                .values(
                    state=TRANSITION_STATE_REJECTED,
                    claim_owner="",
                    claim_expires_at=0.0,
                    last_heartbeat_at=now,
                    last_error=reason,
                    revision=transition.revision + 1,
                    updated_at=now,
                )
            )
            if int(transition_result.rowcount or 0) != 1:
                raise WorkflowTransitionPersistenceError("workflow_transition_lease_conflict")
            self._fault_injector("reject_before_commit")
            session.flush()
            session.expire_all()
            refreshed = session.get(WorkflowTransitionOutboxDB, transition.transition_id)
            if refreshed is None:  # pragma: no cover
                raise WorkflowTransitionPersistenceError("workflow_transition_not_found")
            return _sql_snapshot(session, refreshed)

    def finalize(
        self,
        transition_id: str,
        *,
        owner_id: str,
        claim_generation: int,
        binding_status: Mapping[str, Any],
        checkpoint_ref: str,
        finalization_proof: Mapping[str, Any],
        outcome_fingerprint: str = "",
        receipt_result: Mapping[str, Any] | None = None,
    ) -> WorkflowTransitionSnapshot:
        owner = _owner_id(owner_id)
        generation = _generation(claim_generation)
        now = float(self._clock())
        with self._transaction() as session:
            transition_row = self._locked_transition(session, transition_id)
            _assert_sql_owned(
                transition_row,
                owner_id=owner,
                claim_generation=generation,
                now=now,
            )
            transition = _transition_from_row(transition_row)
            effect_rows = (
                session.execute(
                    self._for_update(
                        sa.select(WorkflowTransitionEffectDB)
                        .where(WorkflowTransitionEffectDB.transition_id == transition.transition_id)
                        .order_by(WorkflowTransitionEffectDB.ordinal.asc())
                    )
                )
                .scalars()
                .all()
            )
            effects = tuple(_effect_from_row(row) for row in effect_rows)
            binding = session.execute(
                self._for_update(
                    sa.select(WorkflowControlBindingDB).where(WorkflowControlBindingDB.id == transition.workflow_id)
                )
            ).scalar_one_or_none()
            _assert_binding_finalize_state(binding, transition)
            receipt = (
                session.execute(
                    self._for_update(
                        sa.select(WorkflowControlCommandReceiptDB).where(
                            WorkflowControlCommandReceiptDB.id == transition.receipt_id
                        )
                    )
                ).scalar_one_or_none()
                if transition.receipt_id
                else None
            )
            _assert_receipt_finalize_state(receipt, transition)
            public_status = _project_public_status(
                self._receipt_projector,
                transition=transition,
                binding=_sql_binding_projection_context(binding),
                binding_status=binding_status,
                receipt_result=receipt_result,
            )
            status, public_status, completed_outcome, completed_effects = _finalization_values(
                transition,
                effects,
                binding_status=binding_status,
                checkpoint_ref=checkpoint_ref,
                finalization_proof=finalization_proof,
                outcome_fingerprint=outcome_fingerprint,
                public_status=public_status,
                claim_generation=generation,
                now=now,
            )

            self._fault_injector("finalize_before_binding_cas")
            binding_result = session.execute(
                sa.update(WorkflowControlBindingDB)
                .where(
                    WorkflowControlBindingDB.id == transition.workflow_id,
                    WorkflowControlBindingDB.revision == int(binding.revision),
                    WorkflowControlBindingDB.active_transition_id == transition.transition_id,
                    WorkflowControlBindingDB.runtime_revision == transition.expected_revision,
                    WorkflowControlBindingDB.runtime_checkpoint_ref == transition.expected_checkpoint_ref,
                    WorkflowControlBindingDB.command_receipt_id == transition.receipt_id,
                )
                .values(
                    last_status=status,
                    public_status=public_status,
                    runtime_revision=_status_revision(status),
                    runtime_checkpoint_ref=checkpoint_ref,
                    active_transition_id="",
                    command_receipt_id="" if transition.receipt_id else binding.command_receipt_id,
                    last_transition_id=transition.transition_id,
                    last_transition_command_id=transition.command_id,
                    last_transition_request_fingerprint=transition.request_fingerprint,
                    last_transition_effect_fingerprint=transition.effect_fingerprint,
                    last_transition_outcome_fingerprint=completed_outcome,
                    revision=int(binding.revision) + 1,
                    updated_at=now,
                )
            )
            if int(binding_result.rowcount or 0) != 1:
                raise WorkflowTransitionPersistenceError("workflow_transition_binding_cas_conflict")
            self._fault_injector("finalize_after_binding_cas")

            if receipt is not None:
                receipt_update = session.execute(
                    sa.update(WorkflowControlCommandReceiptDB)
                    .where(
                        WorkflowControlCommandReceiptDB.id == transition.receipt_id,
                        WorkflowControlCommandReceiptDB.revision == int(receipt.revision),
                        WorkflowControlCommandReceiptDB.transition_id == transition.transition_id,
                        WorkflowControlCommandReceiptDB.request_fingerprint == transition.request_fingerprint,
                        WorkflowControlCommandReceiptDB.effect_fingerprint == transition.effect_fingerprint,
                        WorkflowControlCommandReceiptDB.state == "dispatching",
                        WorkflowControlCommandReceiptDB.dispatch_owner == owner,
                        WorkflowControlCommandReceiptDB.dispatch_generation == generation,
                        WorkflowControlCommandReceiptDB.dispatch_lease_expires_at == transition.claim_expires_at,
                        WorkflowControlCommandReceiptDB.last_heartbeat_at == transition.last_heartbeat_at,
                    )
                    .values(
                        state="completed",
                        result_status=public_status,
                        outcome_fingerprint=completed_outcome,
                        dispatch_owner="",
                        dispatch_lease_expires_at=0.0,
                        dispatch_generation=generation,
                        last_heartbeat_at=now,
                        revision=int(receipt.revision) + 1,
                        updated_at=now,
                    )
                )
                if int(receipt_update.rowcount or 0) != 1:
                    raise WorkflowTransitionPersistenceError("workflow_transition_receipt_cas_conflict")
                self._fault_injector("finalize_after_receipt_cas")

            final_row = effect_rows[-1]
            final_effect = completed_effects[-1]
            effect_result = session.execute(
                sa.update(WorkflowTransitionEffectDB)
                .where(
                    WorkflowTransitionEffectDB.id == final_row.id,
                    WorkflowTransitionEffectDB.transition_id == transition.transition_id,
                    WorkflowTransitionEffectDB.revision == int(final_row.revision),
                    WorkflowTransitionEffectDB.state == final_row.state,
                    WorkflowTransitionEffectDB.applied_generation == int(final_row.applied_generation),
                )
                .values(
                    state=final_effect.state,
                    applied_generation=final_effect.applied_generation,
                    result_payload=thaw_json(final_effect.result_payload),
                    result_digest=final_effect.result_digest,
                    revision=final_effect.revision,
                    updated_at=final_effect.updated_at,
                )
            )
            if int(effect_result.rowcount or 0) != 1:
                raise WorkflowTransitionPersistenceError("workflow_transition_effect_cas_conflict")

            self._fault_injector("finalize_before_transition_cas")
            transition_result = session.execute(
                sa.update(WorkflowTransitionOutboxDB)
                .where(
                    WorkflowTransitionOutboxDB.id == transition.transition_id,
                    WorkflowTransitionOutboxDB.revision == transition.revision,
                    WorkflowTransitionOutboxDB.state == TRANSITION_STATE_APPLYING,
                    WorkflowTransitionOutboxDB.claim_owner == owner,
                    WorkflowTransitionOutboxDB.claim_generation == generation,
                    WorkflowTransitionOutboxDB.claim_expires_at > now,
                )
                .values(
                    state=TRANSITION_STATE_COMPLETED,
                    result_status=status,
                    result_checkpoint_ref=checkpoint_ref,
                    outcome_fingerprint=completed_outcome,
                    claim_owner="",
                    claim_expires_at=0.0,
                    last_heartbeat_at=now,
                    revision=transition.revision + 1,
                    updated_at=now,
                    completed_at=now,
                )
            )
            if int(transition_result.rowcount or 0) != 1:
                raise WorkflowTransitionPersistenceError("workflow_transition_lease_conflict")
            session.flush()
            session.expire_all()
            refreshed = session.get(WorkflowTransitionOutboxDB, transition.transition_id)
            if refreshed is None:  # pragma: no cover
                raise WorkflowTransitionPersistenceError("workflow_transition_not_found")
            return _sql_snapshot(session, refreshed)


def _transition_row(value: WorkflowTransition) -> WorkflowTransitionOutboxDB:
    return WorkflowTransitionOutboxDB(
        id=value.transition_id,
        tenant_id=value.tenant_id,
        workflow_id=value.workflow_id,
        run_id=value.run_id,
        runtime_id=value.runtime_id,
        kind=value.kind,
        request_payload=thaw_json(value.request_payload),
        command_id=value.command_id or None,
        receipt_id=value.receipt_id or None,
        request_fingerprint=value.request_fingerprint,
        admitted_command_digest=value.admitted_command_digest,
        effect_fingerprint=value.effect_fingerprint,
        outcome_fingerprint=value.outcome_fingerprint,
        expected_revision=value.expected_revision,
        expected_checkpoint_ref=value.expected_checkpoint_ref,
        result_status=thaw_json(value.result_status),
        result_checkpoint_ref=value.result_checkpoint_ref,
        state=value.state,
        available_at=value.available_at,
        claim_owner=value.claim_owner,
        claim_generation=value.claim_generation,
        claim_expires_at=value.claim_expires_at,
        last_heartbeat_at=value.last_heartbeat_at,
        attempt_count=value.attempt_count,
        last_error=value.last_error,
        revision=value.revision,
        created_at=value.created_at,
        updated_at=value.updated_at,
        completed_at=value.completed_at,
    )


def _effect_row(value: WorkflowTransitionEffect) -> WorkflowTransitionEffectDB:
    return WorkflowTransitionEffectDB(
        id=value.effect_id,
        transition_id=value.transition_id,
        ordinal=value.ordinal,
        kind=value.kind,
        idempotency_key=value.idempotency_key,
        payload=thaw_json(value.payload),
        payload_digest=value.payload_digest,
        state=value.state,
        applied_generation=value.applied_generation,
        result_payload=thaw_json(value.result_payload),
        result_digest=value.result_digest,
        revision=value.revision,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def _transition_from_row(row: WorkflowTransitionOutboxDB) -> WorkflowTransition:
    if int(row.attempt_count) != int(row.claim_generation):
        raise WorkflowTransitionPersistenceError("workflow_transition_header_attempt_conflict")
    return WorkflowTransition(
        transition_id=str(row.id),
        tenant_id=str(row.tenant_id),
        workflow_id=str(row.workflow_id),
        run_id=str(row.run_id),
        runtime_id=str(row.runtime_id),
        kind=str(row.kind),
        request_payload=dict(row.request_payload or {}),
        command_id=str(row.command_id or ""),
        receipt_id=str(row.receipt_id or ""),
        request_fingerprint=str(row.request_fingerprint),
        admitted_command_digest=str(row.admitted_command_digest or ""),
        effect_fingerprint=str(row.effect_fingerprint),
        outcome_fingerprint=str(row.outcome_fingerprint or ""),
        expected_revision=int(row.expected_revision),
        expected_checkpoint_ref=str(row.expected_checkpoint_ref),
        result_status=dict(row.result_status or {}),
        result_checkpoint_ref=str(row.result_checkpoint_ref or ""),
        state=str(row.state),
        available_at=float(row.available_at),
        claim_owner=str(row.claim_owner or ""),
        claim_generation=int(row.claim_generation),
        claim_expires_at=float(row.claim_expires_at),
        last_heartbeat_at=float(row.last_heartbeat_at),
        attempt_count=int(row.attempt_count),
        last_error=str(row.last_error or ""),
        revision=int(row.revision),
        created_at=float(row.created_at),
        updated_at=float(row.updated_at),
        completed_at=float(row.completed_at),
    )


def _effect_from_row(row: WorkflowTransitionEffectDB) -> WorkflowTransitionEffect:
    return WorkflowTransitionEffect(
        effect_id=str(row.id),
        transition_id=str(row.transition_id),
        ordinal=int(row.ordinal),
        kind=str(row.kind),
        idempotency_key=str(row.idempotency_key),
        payload=dict(row.payload or {}),
        payload_digest=str(row.payload_digest),
        state=str(row.state),
        applied_generation=int(row.applied_generation),
        result_payload=dict(row.result_payload or {}),
        result_digest=str(row.result_digest or ""),
        revision=int(row.revision),
        created_at=float(row.created_at),
        updated_at=float(row.updated_at),
    )


def _sql_snapshot(session: Any, row: WorkflowTransitionOutboxDB) -> WorkflowTransitionSnapshot:
    effects = (
        session.execute(
            sa.select(WorkflowTransitionEffectDB)
            .where(WorkflowTransitionEffectDB.transition_id == row.id)
            .order_by(WorkflowTransitionEffectDB.ordinal.asc())
        )
        .scalars()
        .all()
    )
    return WorkflowTransitionSnapshot(
        _transition_from_row(row),
        tuple(_effect_from_row(effect) for effect in effects),
    )


def _same_snapshot_or_raise(
    existing: WorkflowTransitionSnapshot,
    transition: WorkflowTransition,
    effects: Sequence[WorkflowTransitionEffect],
) -> WorkflowTransitionSnapshot:
    existing_plan = existing.transition.to_dict()
    requested_plan = transition.to_dict()
    mutable_fields = {
        "state",
        "result_status",
        "result_checkpoint_ref",
        "outcome_fingerprint",
        "claim_owner",
        "claim_generation",
        "claim_expires_at",
        "last_heartbeat_at",
        "attempt_count",
        "available_at",
        "last_error",
        "revision",
        "created_at",
        "updated_at",
        "completed_at",
    }
    for field_name in mutable_fields:
        existing_plan.pop(field_name, None)
        requested_plan.pop(field_name, None)
    existing_effects = [
        {
            key: value
            for key, value in effect.to_dict().items()
            if key
            not in {
                "state",
                "applied_generation",
                "result_payload",
                "result_digest",
                "revision",
                "created_at",
                "updated_at",
            }
        }
        for effect in existing.effects
    ]
    requested_effects = [
        {
            key: value
            for key, value in effect.to_dict().items()
            if key
            not in {
                "state",
                "applied_generation",
                "result_payload",
                "result_digest",
                "revision",
                "created_at",
                "updated_at",
            }
        }
        for effect in effects
    ]
    if canonical_json(existing_plan) != canonical_json(requested_plan) or canonical_json(
        existing_effects
    ) != canonical_json(requested_effects):
        raise WorkflowTransitionPersistenceError("workflow_transition_stage_conflict")
    return existing


def _linked_receipt(transition: WorkflowTransition, receipt_id: str) -> str:
    explicit = str(receipt_id or "")
    if explicit and explicit != transition.receipt_id:
        raise WorkflowTransitionPersistenceError("workflow_transition_receipt_binding_invalid")
    return transition.receipt_id


def _assert_memory_binding_for_stage(
    binding: Mapping[str, Any] | None,
    *,
    transition: WorkflowTransition,
    receipt_id: str,
    now: float,
) -> None:
    if binding is None:
        raise WorkflowTransitionPersistenceError("workflow_transition_binding_not_found")
    if (
        binding.get("tenant_id") != transition.tenant_id
        or binding.get("workflow_id") != transition.workflow_id
        or binding.get("run_id") != transition.run_id
        or not _runtime_matches(str(binding.get("runtime_id") or ""), transition.runtime_id)
    ):
        raise WorkflowTransitionPersistenceError("workflow_transition_binding_mismatch")
    if (
        int(binding.get("runtime_revision") or 0) != transition.expected_revision
        or str(binding.get("runtime_checkpoint_ref") or "") != transition.expected_checkpoint_ref
        or str(binding.get("active_transition_id") or "")
        or str(binding.get("dispatch_intent_id") or "")
        or str(binding.get("command_claim") or "")
        or bool(binding.get("command_observation_pending"))
        or str(binding.get("command_receipt_id") or "") != receipt_id
        or (str(binding.get("scheduler_owner") or "") and float(binding.get("scheduler_lease_expires_at") or 0.0) > now)
    ):
        raise WorkflowTransitionPersistenceError("workflow_transition_stage_cas_conflict")


def _assert_sql_binding_for_stage(
    binding: WorkflowControlBindingDB | None,
    *,
    transition: WorkflowTransition,
    receipt_id: str,
    now: float,
) -> None:
    if binding is None:
        raise WorkflowTransitionPersistenceError("workflow_transition_binding_not_found")
    if (
        binding.tenant_id != transition.tenant_id
        or binding.workflow_id != transition.workflow_id
        or binding.run_id != transition.run_id
        or not _runtime_matches(str(binding.runtime_id), transition.runtime_id)
    ):
        raise WorkflowTransitionPersistenceError("workflow_transition_binding_mismatch")
    if (
        int(binding.runtime_revision) != transition.expected_revision
        or str(binding.runtime_checkpoint_ref) != transition.expected_checkpoint_ref
        or str(binding.active_transition_id or "")
        or str(binding.dispatch_intent_id or "")
        or str(binding.command_claim or "")
        or bool(binding.command_observation_pending)
        or str(binding.command_receipt_id or "") != receipt_id
        or (str(binding.scheduler_owner or "") and float(binding.scheduler_lease_expires_at) > now)
    ):
        raise WorkflowTransitionPersistenceError("workflow_transition_stage_cas_conflict")


def _assert_memory_receipt_for_stage(
    receipt: Mapping[str, Any] | None,
    *,
    transition: WorkflowTransition,
    now: float,
) -> None:
    if not transition.receipt_id:
        return
    if receipt is None:
        raise WorkflowTransitionPersistenceError("workflow_transition_receipt_not_found")
    if (
        receipt.get("id") != transition.receipt_id
        or receipt.get("tenant_id") != transition.tenant_id
        or receipt.get("workflow_id") != transition.workflow_id
        or receipt.get("run_id") != transition.run_id
        or int(receipt.get("expected_revision") or 0) != transition.expected_revision
        or receipt.get("checkpoint_ref") != transition.expected_checkpoint_ref
        or workflow_transition_request_fingerprint(receipt.get("request_payload") or {})
        != transition.request_fingerprint
        or str(receipt.get("request_fingerprint") or "") not in {"", transition.request_fingerprint}
        or receipt.get("state") != "pending"
        or str(receipt.get("transition_id") or "")
        or str(receipt.get("effect_fingerprint") or "")
        or str(receipt.get("outcome_fingerprint") or "")
        or str(receipt.get("dispatch_owner") or "")
        or float(receipt.get("dispatch_lease_expires_at") or 0.0) != 0.0
        or int(receipt.get("dispatch_generation") or 0) != 0
        or float(receipt.get("last_heartbeat_at") or 0.0) != 0.0
    ):
        raise WorkflowTransitionPersistenceError("workflow_transition_receipt_stage_conflict")


def _assert_sql_receipt_for_stage(
    receipt: WorkflowControlCommandReceiptDB | None,
    *,
    transition: WorkflowTransition,
    now: float,
) -> None:
    if not transition.receipt_id:
        return
    if receipt is None:
        raise WorkflowTransitionPersistenceError("workflow_transition_receipt_not_found")
    if (
        receipt.id != transition.receipt_id
        or receipt.tenant_id != transition.tenant_id
        or receipt.workflow_id != transition.workflow_id
        or receipt.run_id != transition.run_id
        or int(receipt.expected_revision) != transition.expected_revision
        or receipt.checkpoint_ref != transition.expected_checkpoint_ref
        or workflow_transition_request_fingerprint(receipt.request_payload or {}) != transition.request_fingerprint
        or str(receipt.request_fingerprint or "") not in {"", transition.request_fingerprint}
        or receipt.state != "pending"
        or str(receipt.transition_id or "")
        or str(receipt.effect_fingerprint or "")
        or str(receipt.outcome_fingerprint or "")
        or str(receipt.dispatch_owner or "")
        or float(receipt.dispatch_lease_expires_at) != 0.0
        or int(receipt.dispatch_generation) != 0
        or float(receipt.last_heartbeat_at) != 0.0
    ):
        raise WorkflowTransitionPersistenceError("workflow_transition_receipt_stage_conflict")


def _assert_owned(
    transition: WorkflowTransition,
    *,
    owner_id: str,
    claim_generation: int,
    now: float,
) -> None:
    if (
        transition.state != TRANSITION_STATE_APPLYING
        or transition.claim_owner != _owner_id(owner_id)
        or transition.claim_generation != _generation(claim_generation)
        or transition.claim_expires_at <= now
    ):
        raise WorkflowTransitionPersistenceError("workflow_transition_lease_conflict")


def _assert_sql_owned(
    row: WorkflowTransitionOutboxDB | None,
    *,
    owner_id: str,
    claim_generation: int,
    now: float,
) -> None:
    if row is None:
        raise WorkflowTransitionPersistenceError("workflow_transition_not_found")
    if (
        row.state != TRANSITION_STATE_APPLYING
        or row.claim_owner != _owner_id(owner_id)
        or int(row.claim_generation) != _generation(claim_generation)
        or float(row.claim_expires_at) <= now
    ):
        raise WorkflowTransitionPersistenceError("workflow_transition_lease_conflict")


def _assert_binding_finalize_state(
    binding: Mapping[str, Any] | WorkflowControlBindingDB | None,
    transition: WorkflowTransition,
) -> None:
    _assert_binding_execution_state(binding, transition)


def _assert_binding_execution_state(
    binding: Mapping[str, Any] | WorkflowControlBindingDB | None,
    transition: WorkflowTransition,
) -> None:
    """Fence authority before send and before any retry/progress requeue."""

    _assert_binding_quarantine_state(binding, transition)
    if binding is None:  # pragma: no cover - guarded above
        raise WorkflowTransitionPersistenceError("workflow_transition_binding_not_found")

    def get(name: str) -> Any:
        return binding.get(name) if isinstance(binding, Mapping) else getattr(binding, name)

    if (
        int(get("runtime_revision") or 0) != transition.expected_revision
        or str(get("runtime_checkpoint_ref") or "") != transition.expected_checkpoint_ref
    ):
        raise WorkflowTransitionPersistenceError("workflow_transition_binding_cas_conflict")


def _assert_binding_quarantine_state(
    binding: Mapping[str, Any] | WorkflowControlBindingDB | None,
    transition: WorkflowTransition,
) -> None:
    """Validate aggregate identity without requiring the possibly-drifted revision."""

    if binding is None:
        raise WorkflowTransitionPersistenceError("workflow_transition_binding_not_found")

    def get(name: str) -> Any:
        return binding.get(name) if isinstance(binding, Mapping) else getattr(binding, name)

    if (
        str(get("active_transition_id") or "") != transition.transition_id
        or str(get("tenant_id") or "") != transition.tenant_id
        or str(get("workflow_id") or "") != transition.workflow_id
        or str(get("run_id") or "") != transition.run_id
        or not _runtime_matches(str(get("runtime_id") or ""), transition.runtime_id)
        or str(get("command_receipt_id") or "") != transition.receipt_id
    ):
        raise WorkflowTransitionPersistenceError("workflow_transition_binding_cas_conflict")


def _assert_receipt_finalize_state(
    receipt: Mapping[str, Any] | WorkflowControlCommandReceiptDB | None,
    transition: WorkflowTransition,
) -> None:
    if transition.state != TRANSITION_STATE_APPLYING:
        raise WorkflowTransitionPersistenceError("workflow_transition_receipt_cas_conflict")
    _assert_receipt_lease_mirror(receipt, transition)


def _assert_receipt_lease_mirror(
    receipt: Mapping[str, Any] | WorkflowControlCommandReceiptDB | None,
    transition: WorkflowTransition,
) -> None:
    if not transition.receipt_id:
        if receipt is not None:
            raise WorkflowTransitionPersistenceError("workflow_transition_receipt_cas_conflict")
        return
    if receipt is None:
        raise WorkflowTransitionPersistenceError("workflow_transition_receipt_not_found")

    def get(name: str) -> Any:
        return receipt.get(name) if isinstance(receipt, Mapping) else getattr(receipt, name)

    receipt_state = {
        TRANSITION_STATE_READY: "pending",
        TRANSITION_STATE_APPLYING: "dispatching",
        TRANSITION_STATE_COMPLETED: "completed",
        TRANSITION_STATE_QUARANTINED: "pending",
        TRANSITION_STATE_REJECTED: "rejected",
    }.get(transition.state)
    expected_owner = transition.claim_owner if transition.state == TRANSITION_STATE_APPLYING else ""
    expected_expiry = transition.claim_expires_at if transition.state == TRANSITION_STATE_APPLYING else 0.0
    if (
        str(get("id") or "") != transition.receipt_id
        or str(get("transition_id") or "") != transition.transition_id
        or str(get("request_fingerprint") or "") != transition.request_fingerprint
        or str(get("effect_fingerprint") or "") != transition.effect_fingerprint
        or str(get("state") or "") != receipt_state
        or str(get("dispatch_owner") or "") != expected_owner
        or int(get("dispatch_generation") or 0) != transition.claim_generation
        or float(get("dispatch_lease_expires_at") or 0.0) != expected_expiry
        or float(get("last_heartbeat_at") or 0.0) != transition.last_heartbeat_at
    ):
        raise WorkflowTransitionPersistenceError("workflow_transition_receipt_cas_conflict")


def _finalization_values(
    transition: WorkflowTransition,
    effects: Sequence[WorkflowTransitionEffect],
    *,
    binding_status: Mapping[str, Any],
    checkpoint_ref: str,
    finalization_proof: Mapping[str, Any],
    outcome_fingerprint: str,
    public_status: Mapping[str, Any],
    claim_generation: int,
    now: float,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    str,
    tuple[WorkflowTransitionEffect, ...],
]:
    supplied_outcome = _optional_outcome_fingerprint(outcome_fingerprint)
    status = _bounded_mapping(binding_status, reason="binding_status", empty=False)
    if not isinstance(checkpoint_ref, str) or not checkpoint_ref or len(checkpoint_ref) > 512:
        raise WorkflowTransitionPersistenceError("workflow_transition_checkpoint_ref_invalid")
    revision = _status_revision(status)
    if revision <= transition.expected_revision:
        raise WorkflowTransitionPersistenceError("workflow_transition_status_revision_not_advanced")
    observed_checkpoint = status.get("checkpoint_ref")
    if not isinstance(observed_checkpoint, str) or observed_checkpoint != checkpoint_ref:
        raise WorkflowTransitionPersistenceError("workflow_transition_status_checkpoint_mismatch")
    canonical_public = _bounded_mapping(
        public_status,
        reason="public_status",
        empty=False,
    )
    if not isinstance(finalization_proof, Mapping):
        raise WorkflowTransitionPersistenceError("workflow_transition_finalization_proof_invalid")
    proof = _bounded_mapping(
        finalization_proof,
        reason="finalization_proof",
        empty=False,
    )
    try:
        finalization_attempts = workflow_transition_finalization_stage_attempt_count(
            transition,
            effects,
        )
        expected_outcome = workflow_transition_outcome_fingerprint(
            transition,
            effects,
            binding_status=status,
            checkpoint_ref=checkpoint_ref,
            finalization_proof=proof,
            public_status=canonical_public,
        )
    except WorkflowTransitionError as exc:
        if str(exc) == "workflow_transition_header_attempt_conflict":
            raise WorkflowTransitionPersistenceError(str(exc)) from exc
        raise WorkflowTransitionPersistenceError("workflow_transition_effect_result_envelope_invalid") from exc
    if supplied_outcome and supplied_outcome != expected_outcome:
        raise WorkflowTransitionPersistenceError("workflow_transition_outcome_fingerprint_mismatch")

    values = list(effects)
    if not values or values[-1].kind != EFFECT_BINDING_FINALIZE:
        raise WorkflowTransitionPersistenceError("workflow_transition_binding_finalize_effect_invalid")
    if any(effect.state != EFFECT_STATE_APPLIED for effect in values[:-1]):
        raise WorkflowTransitionPersistenceError("workflow_transition_effects_incomplete")
    final_effect = values[-1]
    if final_effect.state != EFFECT_STATE_PLANNED or final_effect.applied_generation != 0:
        raise WorkflowTransitionPersistenceError("workflow_transition_binding_finalize_effect_conflict")
    final_result = {
        "checkpoint_ref": checkpoint_ref,
        "finalization_stage_attempt_count": finalization_attempts,
        "finalization_proof": proof,
        "outcome_fingerprint": expected_outcome,
        "public_status": canonical_public,
        "receipt_completed": bool(transition.receipt_id),
        "status_revision": revision,
    }
    values[-1] = replace(
        final_effect,
        state=EFFECT_STATE_APPLIED,
        applied_generation=_generation(claim_generation),
        result_payload=final_result,
        result_digest=workflow_transition_finalization_result_digest(final_result),
        revision=final_effect.revision + 1,
        updated_at=now,
    )
    return status, canonical_public, expected_outcome, tuple(values)


def _project_public_status(
    projector: WorkflowTransitionPublicProjectionPort | None,
    *,
    transition: WorkflowTransition,
    binding: Mapping[str, Any],
    binding_status: Mapping[str, Any],
    receipt_result: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if projector is None:
        raise WorkflowTransitionPersistenceError("workflow_transition_receipt_projector_required")
    context = _bounded_mapping(binding, reason="binding_projection_context", empty=False)
    raw_status = _bounded_mapping(binding_status, reason="binding_status", empty=False)
    previous_value = context.get("public_status")
    previous = (
        _bounded_mapping(previous_value, reason="previous_public_status", empty=True)
        if isinstance(previous_value, Mapping)
        else None
    )
    try:
        projected = projector.project(
            transition=transition,
            binding=context,
            binding_status=raw_status,
            previous_public_status=previous or None,
        )
        canonical = _bounded_mapping(projected, reason="public_projection", empty=False)
    except WorkflowTransitionPersistenceError:
        raise
    except (KeyError, RuntimeError, TypeError, ValueError) as exc:
        raise WorkflowTransitionPersistenceError("workflow_transition_receipt_projection_invalid") from exc
    if receipt_result is not None:
        supplied = _bounded_mapping(receipt_result, reason="receipt_result", empty=False)
        if canonical_json(canonical) != canonical_json(supplied):
            raise WorkflowTransitionPersistenceError("workflow_transition_receipt_projection_mismatch")
    return canonical


def _sql_binding_projection_context(binding: WorkflowControlBindingDB) -> dict[str, Any]:
    return {
        "tenant_id": str(binding.tenant_id),
        "subject_id": str(binding.subject_id),
        "workflow_id": str(binding.workflow_id),
        "run_id": str(binding.run_id),
        "runtime_id": str(binding.runtime_id),
        "plan_hash": str(binding.plan_hash),
        "policy_version": str(binding.policy_version),
        "checkpoint_id": str(binding.checkpoint_id),
        "workflow_request": _mapping_copy(binding.workflow_request),
        "execution_plan": _mapping_copy(binding.execution_plan or {}),
        "last_status": _mapping_copy(binding.last_status or {}),
        "public_status": _mapping_copy(binding.public_status or {}),
        "runtime_revision": int(binding.runtime_revision),
        "runtime_checkpoint_ref": str(binding.runtime_checkpoint_ref),
    }


def _effect(
    effects: Sequence[WorkflowTransitionEffect],
    effect_id: str,
) -> tuple[int, WorkflowTransitionEffect]:
    for index, value in enumerate(effects):
        if value.effect_id == str(effect_id):
            return index, value
    raise WorkflowTransitionPersistenceError("workflow_transition_effect_not_found")


def _assert_effect_rejection_safe(
    effects: Sequence[WorkflowTransitionEffect],
) -> None:
    if any(effect.state != EFFECT_STATE_PLANNED for effect in effects):
        raise WorkflowTransitionPersistenceError("workflow_transition_effect_recovery_required")


def _assert_effect_begin_order(
    effects: Sequence[WorkflowTransitionEffect],
    *,
    effect_index: int,
) -> None:
    """Deny execution authority when a later non-final stage already progressed."""

    if any(
        effect.kind != EFFECT_BINDING_FINALIZE and effect.state != EFFECT_STATE_PLANNED
        for effect in effects[effect_index + 1 :]
    ):
        raise WorkflowTransitionPersistenceError("workflow_transition_effect_order_conflict")


def _assert_yield_effect(
    effects: Sequence[WorkflowTransitionEffect],
    *,
    effect_id: str,
    claim_generation: int,
) -> None:
    _index, effect = _effect(effects, str(effect_id))
    generation = _generation(claim_generation)
    current_generation = tuple(
        candidate
        for candidate in effects
        if candidate.state == EFFECT_STATE_APPLIED and candidate.applied_generation == generation
    )
    if (
        effect.kind == EFFECT_BINDING_FINALIZE
        or effect.state != EFFECT_STATE_APPLIED
        or effect.applied_generation != generation
        or current_generation != (effect,)
    ):
        raise WorkflowTransitionPersistenceError("workflow_transition_yield_effect_conflict")


def _assert_effect_stage_attempt(
    transition: WorkflowTransition,
    effects: Sequence[WorkflowTransitionEffect],
    *,
    effect_index: int,
    result_payload: Mapping[str, Any],
) -> None:
    try:
        if transition.attempt_count != transition.claim_generation:
            raise WorkflowTransitionError("workflow_transition_header_attempt_conflict")
        previous_generation = 0
        for effect in effects[:effect_index]:
            if effect.kind == EFFECT_BINDING_FINALIZE or effect.state != EFFECT_STATE_APPLIED:
                continue
            if (
                effect.applied_generation <= previous_generation
                or effect.applied_generation >= transition.claim_generation
            ):
                raise WorkflowTransitionError("workflow_transition_effect_application_generation_invalid")
            stage_attempts = workflow_transition_effect_stage_attempt_count(effect.result_payload)
            if stage_attempts != effect.applied_generation - previous_generation:
                raise WorkflowTransitionError("workflow_transition_effect_stage_attempt_invalid")
            previous_generation = effect.applied_generation
        supplied = workflow_transition_effect_stage_attempt_count(result_payload)
    except WorkflowTransitionError as exc:
        raise WorkflowTransitionPersistenceError("workflow_transition_effect_result_envelope_invalid") from exc
    expected = transition.claim_generation - previous_generation
    if expected < 1 or supplied != expected:
        raise WorkflowTransitionPersistenceError("workflow_transition_effect_stage_attempt_conflict")


def _claimable(transition: WorkflowTransition, *, now: float) -> bool:
    return bool(
        transition.available_at <= now
        and (
            transition.state == TRANSITION_STATE_READY
            or (transition.state == TRANSITION_STATE_APPLYING and transition.claim_expires_at <= now)
        )
    )


def _row_claimable(row: WorkflowTransitionOutboxDB, *, now: float) -> bool:
    return bool(
        float(row.available_at) <= now
        and (
            row.state == TRANSITION_STATE_READY
            or (row.state == TRANSITION_STATE_APPLYING and float(row.claim_expires_at) <= now)
        )
    )


def _runtime_matches(binding_runtime: str, transition_runtime: str) -> bool:
    if binding_runtime == transition_runtime:
        return True
    return binding_runtime == "local" and transition_runtime == "ananta-native"


def _result_payload(
    value: Mapping[str, Any],
    *,
    result_digest: str,
) -> dict[str, Any]:
    safe = _bounded_mapping(value, reason="effect_result", empty=False)
    if workflow_transition_effect_result_digest(safe) != str(result_digest):
        raise WorkflowTransitionPersistenceError("workflow_transition_effect_result_digest_mismatch")
    try:
        workflow_transition_effect_stage_attempt_count(safe)
    except WorkflowTransitionError as exc:
        raise WorkflowTransitionPersistenceError("workflow_transition_effect_result_envelope_invalid") from exc
    return safe


def _bounded_mapping(
    value: Mapping[str, Any],
    *,
    reason: str,
    empty: bool,
) -> dict[str, Any]:
    safe = _mapping_copy(value)
    if not isinstance(safe, dict) or (not empty and not safe):
        raise WorkflowTransitionPersistenceError(f"workflow_transition_{reason}_invalid")
    try:
        size = len(canonical_json(safe).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise WorkflowTransitionPersistenceError(f"workflow_transition_{reason}_invalid") from exc
    if size > _MAX_RESULT_BYTES:
        raise WorkflowTransitionPersistenceError(f"workflow_transition_{reason}_too_large")
    return safe


def _mapping_copy(value: Mapping[str, Any]) -> dict[str, Any]:
    thawed = thaw_json(value)
    if not isinstance(thawed, dict):
        raise WorkflowTransitionPersistenceError("workflow_transition_mapping_invalid")
    return thawed


def _status_revision(status: Mapping[str, Any]) -> int:
    value = status.get("revision")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise WorkflowTransitionPersistenceError("workflow_transition_status_revision_invalid")
    return value


def _owner_id(value: Any) -> str:
    normalized = str(value or "").strip()
    if (
        not normalized
        or len(normalized) > 256
        or "\x00" in normalized
        or any(not character.isprintable() for character in normalized)
    ):
        raise WorkflowTransitionPersistenceError("workflow_transition_owner_id_invalid")
    return normalized


def _generation(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise WorkflowTransitionPersistenceError("workflow_transition_claim_generation_invalid")
    return value


def _lease_seconds(value: Any) -> float:
    if isinstance(value, bool):
        raise WorkflowTransitionPersistenceError("workflow_transition_lease_invalid")
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise WorkflowTransitionPersistenceError("workflow_transition_lease_invalid") from exc
    if not math.isfinite(normalized) or not 1.0 <= normalized <= _MAX_LEASE_SECONDS:
        raise WorkflowTransitionPersistenceError("workflow_transition_lease_invalid")
    return normalized


def _retry_at(value: Any) -> float:
    if isinstance(value, bool):
        raise WorkflowTransitionPersistenceError("workflow_transition_retry_at_invalid")
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise WorkflowTransitionPersistenceError("workflow_transition_retry_at_invalid") from exc
    if not math.isfinite(normalized) or normalized < 0:
        raise WorkflowTransitionPersistenceError("workflow_transition_retry_at_invalid")
    return normalized


def _optional_outcome_fingerprint(value: Any) -> str:
    if not isinstance(value, str):
        raise WorkflowTransitionPersistenceError("workflow_transition_outcome_fingerprint_invalid")
    if value and (len(value) != 64 or any(character not in "0123456789abcdef" for character in value)):
        raise WorkflowTransitionPersistenceError("workflow_transition_outcome_fingerprint_invalid")
    return value


def _reason_code(value: Any) -> str:
    normalized = str(value or "").strip()
    if (
        not normalized
        or len(normalized) > 160
        or not normalized[0].islower()
        or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_" for character in normalized)
    ):
        raise WorkflowTransitionPersistenceError("workflow_transition_reason_code_invalid")
    return normalized


def _limit(value: Any) -> int:
    if isinstance(value, bool):
        raise WorkflowTransitionPersistenceError("workflow_transition_limit_invalid")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise WorkflowTransitionPersistenceError("workflow_transition_limit_invalid") from exc
    if not 1 <= normalized <= 1000:
        raise WorkflowTransitionPersistenceError("workflow_transition_limit_invalid")
    return normalized


__all__ = [
    "InMemoryWorkflowTransitionStore",
    "SQLAlchemyWorkflowTransitionStore",
    "WorkflowTransitionPersistenceError",
]
