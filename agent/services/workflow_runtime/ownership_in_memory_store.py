"""In-memory execution ownership store for tests and single-process runtimes."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import replace
from typing import Any

from agent.services.workflow_runtime.errors import FencingTokenError, InvalidTransitionError, OptimisticConcurrencyError
from agent.services.workflow_runtime.ownership_records import (
    ExecutionOwnership,
    OwnershipClaim,
    RetryBudgetSnapshot,
    _assert_expected_revision,
    _assert_owner,
    _timestamp,
    _validate_lease,
)
from agent.services.workflow_runtime.ownership_transition_errors import WorkflowTransitionOwnershipReservationConflict
from agent.services.workflow_runtime.ownership_transition_projection import (
    _transition_ownership_evidence,
    _transition_ownership_observation,
    _transition_ownership_relevant_receipts,
    _transition_ownership_reservation_values,
)
from agent.services.workflow_runtime.ownership_transition_reservation import (
    WorkflowTransitionOwnershipReservationEvidence,
    WorkflowTransitionOwnershipReservationIntent,
    WorkflowTransitionOwnershipReservationObservation,
    WorkflowTransitionOwnershipReservationReceipt,
    WorkflowTransitionOwnershipRetryConsumption,
)
from ananta_contracts.hub_task_gateway import RETRY_CATEGORIES


class InMemoryExecutionOwnershipStore:
    def acknowledge_result_fenced(self, *, lease, **values):
        from agent.services.workflow_runtime.lease_fencing import ownership_lease_recipient

        return lease.store.mutate_fenced(
            lease=lease,
            recipient=ownership_lease_recipient(values, lease),
            mutation=lambda: self.acknowledge_result(**values),
        )

    def fail_attempt_fenced(self, *, lease, **values):
        from agent.services.workflow_runtime.lease_fencing import ownership_lease_recipient

        return lease.store.mutate_fenced(
            lease=lease,
            recipient=ownership_lease_recipient(values, lease),
            mutation=lambda: self.fail_attempt(**values),
        )

    def __init__(self) -> None:
        self._current: dict[tuple[str, str, str], ExecutionOwnership] = {}
        self._history: dict[tuple[str, str, str], list[ExecutionOwnership]] = {}
        self._retry_used: dict[tuple[str, str], int] = {}
        self._retry_maximum: dict[tuple[str, str], int] = {}
        self._retry_ids: dict[tuple[str, str, str], str] = {}
        self._transition_reservation_receipts: dict[str, WorkflowTransitionOwnershipReservationReceipt] = {}
        self._lock = threading.RLock()

    def claim(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        step_id: str,
        owner_id: str,
        lease_seconds: float,
        maximum_retries: int,
        now: float | None = None,
    ) -> OwnershipClaim:
        timestamp = _validate_lease(lease_seconds, now)
        key = (str(tenant_id), str(run_id), str(step_id))
        with self._lock:
            current = self._current.get(key)
            if current is not None and current.workflow_id != str(workflow_id):
                raise OptimisticConcurrencyError("execution_ownership_workflow_binding_conflict")
            if current and current.status == "completed":
                return OwnershipClaim(current, False, "already_completed")
            if current and current.status == "active" and current.lease_expires_at > timestamp:
                reason = "already_owned" if current.owner_id == str(owner_id) else "lease_held"
                return OwnershipClaim(current, False, reason)
            next_fence = (current.fencing_token + 1) if current else 1
            attempt_id = f"att-{uuid.uuid4().hex}"
            if current is not None:
                self.consume_retry(
                    tenant_id=tenant_id,
                    run_id=run_id,
                    retry_id=attempt_id,
                    category="hub_task",
                    maximum=maximum_retries,
                )
            ownership = ExecutionOwnership(
                tenant_id=str(tenant_id),
                workflow_id=str(workflow_id),
                run_id=str(run_id),
                step_id=str(step_id),
                attempt_id=attempt_id,
                owner_id=str(owner_id),
                fencing_token=next_fence,
                revision=(current.revision + 1) if current else 1,
                status="active",
                lease_expires_at=timestamp + float(lease_seconds),
                last_heartbeat_at=timestamp,
            )
            ownership.assert_valid()
            self._save(key, ownership)
            return OwnershipClaim(ownership, True, "acquired" if current is None else "recovered")

    def observe_transition_reservation(
        self,
        intent: WorkflowTransitionOwnershipReservationIntent,
        *,
        claim_generation: int,
    ) -> WorkflowTransitionOwnershipReservationObservation:
        with self._lock:
            return self._transition_reservation_observation_unlocked(
                intent,
                claim_generation=claim_generation,
            )

    def read_transition_reservation_history(
        self,
        intent: WorkflowTransitionOwnershipReservationIntent,
    ) -> WorkflowTransitionOwnershipReservationEvidence:
        with self._lock:
            if not isinstance(intent, WorkflowTransitionOwnershipReservationIntent):
                raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_intent_invalid")
            key = (intent.tenant_id, intent.run_id, intent.step_id)
            retry_category = self._retry_ids.get((intent.tenant_id, intent.run_id, intent.retry_id))
            consumption = (
                None
                if retry_category is None
                else WorkflowTransitionOwnershipRetryConsumption(
                    tenant_id=intent.tenant_id,
                    run_id=intent.run_id,
                    retry_id=intent.retry_id,
                    category=retry_category,
                )
            )
            receipts = _transition_ownership_relevant_receipts(
                intent,
                current=None,
                receipts=tuple(self._transition_reservation_receipts.values()),
                include_prospective=False,
            )
            revisions = {
                revision
                for receipt in receipts
                for revision in (
                    receipt.acquired_revision,
                    receipt.prior_ownership.revision if receipt.prior_ownership is not None else 0,
                )
                if revision > 0
            }
            history = tuple(
                value
                for value in self._history.get(key, ())
                if value.revision in revisions or value.attempt_id == intent.attempt_id
            )
            return _transition_ownership_evidence(
                intent,
                history=history,
                retry_consumption=consumption,
                receipts=receipts,
            )

    def reserve_transition_effect(
        self,
        intent: WorkflowTransitionOwnershipReservationIntent,
        *,
        creator_claim_generation: int,
        expected_observation_digest: str,
        reserved_at: float,
    ) -> WorkflowTransitionOwnershipReservationReceipt:
        with self._lock:
            evidence = self.read_transition_reservation_history(intent)
            if evidence.receipt is not None:
                if evidence.receipt.creator_claim_generation > creator_claim_generation:
                    raise WorkflowTransitionOwnershipReservationConflict(
                        "workflow_transition_ownership_receipt_generation_conflict"
                    )
                return evidence.receipt
            observation = self._transition_reservation_observation_unlocked(
                intent,
                claim_generation=creator_claim_generation,
            )
            acquired, consumption, budget, receipt = _transition_ownership_reservation_values(
                observation,
                creator_claim_generation=creator_claim_generation,
                expected_observation_digest=expected_observation_digest,
                reserved_at=reserved_at,
            )
            if observation.receipt is not None:
                return observation.receipt

            key = (intent.tenant_id, intent.run_id, intent.step_id)
            budget_key = (intent.tenant_id, intent.run_id)
            current_values = dict(self._current)
            history_values = {slot: list(values) for slot, values in self._history.items()}
            retry_used = dict(self._retry_used)
            retry_maximum = dict(self._retry_maximum)
            retry_ids = dict(self._retry_ids)
            receipts = dict(self._transition_reservation_receipts)
            self._transition_reservation_fault("before_retry", consumption)
            if consumption is not None:
                retry_key = (intent.tenant_id, intent.run_id, intent.retry_id)
                if retry_key in retry_ids:
                    raise WorkflowTransitionOwnershipReservationConflict(
                        "workflow_transition_ownership_retry_without_receipt"
                    )
                retry_ids[retry_key] = consumption.category
                retry_used[budget_key] = budget.used
                retry_maximum[budget_key] = budget.maximum
            self._transition_reservation_fault("after_retry", consumption)
            current_values[key] = acquired
            self._transition_reservation_fault("after_current", acquired)
            history_values.setdefault(key, []).append(acquired)
            self._transition_reservation_fault("after_history", acquired)
            receipts[receipt.receipt_id] = receipt
            self._transition_reservation_fault("after_receipt", receipt)
            self._transition_reservation_fault("before_commit", receipt)

            previous = (
                self._current,
                self._history,
                self._retry_used,
                self._retry_maximum,
                self._retry_ids,
                self._transition_reservation_receipts,
            )
            try:
                self._current = current_values
                self._history = history_values
                self._retry_used = retry_used
                self._retry_maximum = retry_maximum
                self._retry_ids = retry_ids
                self._transition_reservation_receipts = receipts
            except BaseException:
                (
                    self._current,
                    self._history,
                    self._retry_used,
                    self._retry_maximum,
                    self._retry_ids,
                    self._transition_reservation_receipts,
                ) = previous
                raise
        self._transition_reservation_fault("after_commit", receipt)
        return receipt

    def _transition_reservation_observation_unlocked(
        self,
        intent: WorkflowTransitionOwnershipReservationIntent,
        *,
        claim_generation: int,
    ) -> WorkflowTransitionOwnershipReservationObservation:
        if not isinstance(intent, WorkflowTransitionOwnershipReservationIntent):
            raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_intent_invalid")
        key = (intent.tenant_id, intent.run_id, intent.step_id)
        current = self._current.get(key)
        all_history = tuple(self._history.get(key, ()))
        relevant_receipts = _transition_ownership_relevant_receipts(
            intent,
            current=current,
            receipts=tuple(self._transition_reservation_receipts.values()),
            include_prospective=True,
        )
        anchor_revisions = {
            revision
            for receipt in relevant_receipts
            for revision in (
                receipt.acquired_revision,
                receipt.prior_ownership.revision if receipt.prior_ownership is not None else 0,
            )
            if revision > 0
        }
        if current is not None:
            anchor_revisions.add(current.revision)
        history = tuple(
            value
            for value in all_history
            if value.revision in anchor_revisions or value.attempt_id == intent.attempt_id
        )
        if current is None and not history and all_history:
            history = (all_history[-1],)
        retry_category = self._retry_ids.get((intent.tenant_id, intent.run_id, intent.retry_id))
        consumption = (
            None
            if retry_category is None
            else WorkflowTransitionOwnershipRetryConsumption(
                tenant_id=intent.tenant_id,
                run_id=intent.run_id,
                retry_id=intent.retry_id,
                category=retry_category,
            )
        )
        budget_key = (intent.tenant_id, intent.run_id)
        configured_maximum = self._retry_maximum.get(budget_key)
        maximum = intent.maximum_retries if configured_maximum is None else configured_maximum
        budget = RetryBudgetSnapshot(
            intent.tenant_id,
            intent.run_id,
            used=self._retry_used.get(budget_key, 0),
            maximum=maximum,
        )
        return _transition_ownership_observation(
            intent,
            claim_generation=claim_generation,
            current=current,
            history=history,
            retry_consumption=consumption,
            retry_budget=budget,
            receipts=relevant_receipts,
        )

    def _transition_reservation_fault(self, stage: str, value: object) -> None:
        del stage, value

    def heartbeat(self, **values: Any) -> ExecutionOwnership:
        timestamp = _validate_lease(float(values["lease_seconds"]), values.get("now"))
        with self._lock:
            key, current = self._owned(values)
            _assert_expected_revision(current, int(values["expected_revision"]))
            if current.status != "active":
                raise FencingTokenError("heartbeat_owner_no_longer_active")
            if current.lease_expires_at <= timestamp:
                raise FencingTokenError("ownership_lease_expired")
            updated = replace(
                current,
                revision=current.revision + 1,
                last_heartbeat_at=timestamp,
                lease_expires_at=timestamp + float(values["lease_seconds"]),
            )
            self._save(key, updated)
            return updated

    def acknowledge_result(self, **values: Any) -> ExecutionOwnership:
        result_ack_key = str(values.get("result_ack_key") or "")
        if not result_ack_key:
            raise ValueError("result_ack_key_required")
        timestamp = _timestamp(values.get("now"))
        with self._lock:
            key, current = self._owned(values)
            if current.status == "completed" and current.result_ack_key == result_ack_key:
                return current
            _assert_expected_revision(current, int(values["expected_revision"]))
            if current.status != "active" or current.lease_expires_at <= timestamp:
                raise FencingTokenError("result_owner_not_active")
            updated = replace(
                current,
                revision=current.revision + 1,
                status="completed",
                result_ack_key=result_ack_key,
                lease_expires_at=timestamp,
            )
            self._save(key, updated)
            return updated

    def fail_attempt(self, *, failure_code: str, dead_letter: bool = False, **values: Any) -> ExecutionOwnership:
        with self._lock:
            key, current = self._owned(values)
            _assert_expected_revision(current, int(values["expected_revision"]))
            if current.status != "active":
                raise InvalidTransitionError("failure_requires_active_ownership")
            timestamp = _timestamp(values.get("now"))
            if current.lease_expires_at <= timestamp:
                raise FencingTokenError("failure_owner_lease_expired")
            updated = replace(
                current,
                revision=current.revision + 1,
                status="dead_letter" if dead_letter else "failed",
                failure_code=str(failure_code or "execution_failed"),
                lease_expires_at=timestamp,
            )
            self._save(key, updated)
            return updated

    def reconcile_orphan(
        self, *, tenant_id: str, run_id: str, step_id: str, now: float | None = None
    ) -> ExecutionOwnership | None:
        timestamp = float(now if now is not None else time.time())
        key = (str(tenant_id), str(run_id), str(step_id))
        with self._lock:
            current = self._current.get(key)
            if current is None or current.status != "active" or current.lease_expires_at > timestamp:
                return None
            updated = replace(
                current,
                revision=current.revision + 1,
                status="orphaned",
                failure_code="lease_expired",
            )
            self._save(key, updated)
            return updated

    def get(self, *, tenant_id: str, run_id: str, step_id: str) -> ExecutionOwnership | None:
        with self._lock:
            return self._current.get((str(tenant_id), str(run_id), str(step_id)))

    def consume_retry(
        self,
        *,
        tenant_id: str,
        run_id: str,
        retry_id: str,
        category: str,
        maximum: int,
    ) -> RetryBudgetSnapshot:
        if maximum < 0 or not retry_id or category not in RETRY_CATEGORIES:
            raise ValueError("retry_budget_input_invalid")
        key = (str(tenant_id), str(run_id))
        dedupe = (*key, str(retry_id))
        with self._lock:
            current = self._retry_used.get(key, 0)
            configured_maximum = self._retry_maximum.get(key)
            if configured_maximum is not None and configured_maximum != int(maximum):
                raise InvalidTransitionError("retry_budget_maximum_mismatch")
            existing_category = self._retry_ids.get(dedupe)
            if existing_category is not None and existing_category != str(category):
                raise InvalidTransitionError("retry_budget_retry_id_binding_mismatch")
            if existing_category is not None:
                return RetryBudgetSnapshot(*key, used=current, maximum=int(maximum))
            if current >= int(maximum):
                raise InvalidTransitionError("retry_budget_exhausted")
            self._retry_maximum[key] = int(maximum)
            self._retry_ids[dedupe] = str(category)
            self._retry_used[key] = current + 1
            return RetryBudgetSnapshot(*key, used=current + 1, maximum=int(maximum))

    def get_retry_budget(self, *, tenant_id: str, run_id: str, maximum: int) -> RetryBudgetSnapshot:
        with self._lock:
            used = self._retry_used.get((str(tenant_id), str(run_id)), 0)
            configured_maximum = self._retry_maximum.get((str(tenant_id), str(run_id)))
            if configured_maximum is not None and configured_maximum != int(maximum):
                raise InvalidTransitionError("retry_budget_maximum_mismatch")
        return RetryBudgetSnapshot(str(tenant_id), str(run_id), used=used, maximum=int(maximum))

    def _owned(self, values: dict[str, Any]) -> tuple[tuple[str, str, str], ExecutionOwnership]:
        key = (str(values["tenant_id"]), str(values["run_id"]), str(values["step_id"]))
        current = self._current.get(key)
        if current is None:
            raise KeyError("execution_ownership_not_found")
        _assert_owner(
            current,
            attempt_id=str(values["attempt_id"]),
            owner_id=str(values["owner_id"]),
            fencing_token=int(values["fencing_token"]),
        )
        return key, current

    def _save(self, key: tuple[str, str, str], value: ExecutionOwnership) -> None:
        value.assert_valid()
        self._current[key] = value
        self._history.setdefault(key, []).append(value)
