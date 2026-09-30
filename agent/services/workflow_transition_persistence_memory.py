"""Thread-safe in-memory transition store adapter for tests and local use."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any

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
    WorkflowTransitionPublicProjectionPort,
    WorkflowTransitionSnapshot,
    thaw_json,
    validate_transition_plan,
)
from agent.services.workflow_transition_persistence_rules import (
    _ACTIVE_MARKER_TRANSITION_STATES,
    _RECEIPT_ACTIVE_STATES,
    WorkflowTransitionPersistenceError,
    _assert_binding_execution_state,
    _assert_binding_finalize_state,
    _assert_binding_quarantine_state,
    _assert_effect_begin_order,
    _assert_effect_rejection_safe,
    _assert_effect_stage_attempt,
    _assert_memory_binding_for_stage,
    _assert_memory_receipt_for_stage,
    _assert_owned,
    _assert_receipt_finalize_state,
    _assert_receipt_lease_mirror,
    _assert_yield_effect,
    _claimable,
    _effect,
    _finalization_values,
    _lease_seconds,
    _limit,
    _linked_receipt,
    _mapping_copy,
    _owner_id,
    _project_public_status,
    _reason_code,
    _result_payload,
    _retry_at,
    _same_snapshot_or_raise,
    _status_revision,
)


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
