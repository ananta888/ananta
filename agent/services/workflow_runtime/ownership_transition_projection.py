"""Pure projection of stored ownership rows into transition reservation observations, evidence, and receipt values."""

from __future__ import annotations

from agent.services.workflow_runtime.ownership_records import ExecutionOwnership, RetryBudgetSnapshot
from agent.services.workflow_runtime.ownership_transition_errors import (
    WorkflowTransitionOwnershipReservationConflict,
    WorkflowTransitionOwnershipReservationHeld,
    WorkflowTransitionOwnershipReservationStale,
)
from agent.services.workflow_runtime.ownership_transition_identity import _ownership_observation_digest
from agent.services.workflow_runtime.ownership_transition_reservation import (
    WorkflowTransitionOwnershipReservationEvidence,
    WorkflowTransitionOwnershipReservationIntent,
    WorkflowTransitionOwnershipReservationObservation,
    WorkflowTransitionOwnershipReservationReceipt,
    WorkflowTransitionOwnershipRetryConsumption,
)
from agent.services.workflow_runtime.ownership_values import (
    _OWNERSHIP_MAX_LEGACY_REVISION,
    _ownership_exact_timestamp,
    _ownership_positive_integer,
    _ownership_sha256,
)


def _transition_ownership_relevant_receipts(
    intent: WorkflowTransitionOwnershipReservationIntent,
    *,
    current: ExecutionOwnership | None,
    receipts: tuple[WorkflowTransitionOwnershipReservationReceipt, ...],
    include_prospective: bool,
) -> tuple[WorkflowTransitionOwnershipReservationReceipt, ...]:
    relevant: list[WorkflowTransitionOwnershipReservationReceipt] = []
    prospective_available = (
        include_prospective
        and current is not None
        and (
            current.revision < _OWNERSHIP_MAX_LEGACY_REVISION and current.fencing_token < _OWNERSHIP_MAX_LEGACY_REVISION
        )
    )
    next_revision = current.revision + 1 if prospective_available and current is not None else 1
    next_fencing_token = current.fencing_token + 1 if prospective_available and current is not None else 1
    for receipt in receipts:
        if not isinstance(receipt, WorkflowTransitionOwnershipReservationReceipt):
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_receipt_projection_conflict"
            )
        candidate_alias = (
            receipt.receipt_id == intent.receipt_id
            or receipt.effect_id == intent.effect_id
            or receipt.operation_fence_id == intent.operation_fence_id
            or receipt.attempt_id == intent.attempt_id
        )
        current_alias = current is not None and (
            receipt.attempt_id == current.attempt_id
            or (
                receipt.intent.tenant_id == current.tenant_id
                and receipt.intent.run_id == current.run_id
                and receipt.intent.step_id == current.step_id
                and (
                    receipt.acquired_revision == current.revision
                    or receipt.acquired_fencing_token == current.fencing_token
                )
            )
        )
        same_scope = (
            receipt.intent.tenant_id == intent.tenant_id
            and receipt.intent.run_id == intent.run_id
            and receipt.intent.step_id == intent.step_id
        )
        prospective_alias = (
            include_prospective
            and same_scope
            and (
                current is None
                or (
                    prospective_available
                    and (
                        receipt.acquired_revision >= next_revision
                        or receipt.acquired_fencing_token >= next_fencing_token
                    )
                )
            )
        )
        if candidate_alias or current_alias or prospective_alias:
            relevant.append(receipt)
    if len(relevant) > 16:
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_receipt_alias_limit")
    return tuple(sorted(relevant, key=lambda value: (value.receipt_id, value.receipt_digest)))


def _transition_ownership_observation(
    intent: WorkflowTransitionOwnershipReservationIntent,
    *,
    claim_generation: int,
    current: ExecutionOwnership | None,
    history: tuple[ExecutionOwnership, ...],
    retry_consumption: WorkflowTransitionOwnershipRetryConsumption | None,
    retry_budget: RetryBudgetSnapshot,
    receipts: tuple[WorkflowTransitionOwnershipReservationReceipt, ...],
) -> WorkflowTransitionOwnershipReservationObservation:
    if not isinstance(intent, WorkflowTransitionOwnershipReservationIntent):
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_intent_invalid")
    generation = _ownership_positive_integer(claim_generation, "claim_generation")
    current_copy = None if current is None else ExecutionOwnership.from_exact_mapping(current.to_dict())
    history_copy = tuple(ExecutionOwnership.from_exact_mapping(value.to_dict()) for value in history)
    if len(history_copy) > 1_000:
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_history_limit")
    for value in history_copy:
        if (
            value.tenant_id != intent.tenant_id
            or value.workflow_id != intent.workflow_id
            or value.run_id != intent.run_id
            or value.step_id != intent.step_id
        ):
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_history_binding_conflict"
            )
    if current_copy is not None and (
        current_copy.tenant_id != intent.tenant_id
        or current_copy.workflow_id != intent.workflow_id
        or current_copy.run_id != intent.run_id
        or current_copy.step_id != intent.step_id
    ):
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_current_binding_conflict")
    current_matches = (
        tuple(value for value in history_copy if current_copy is not None and value.revision == current_copy.revision)
        if current_copy is not None
        else ()
    )
    if current_copy is not None and (len(current_matches) != 1 or current_matches[0] != current_copy):
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_current_history_conflict")
    if current_copy is None and history_copy:
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_orphan_history_conflict")
    current_history = current_matches[0] if current_matches else None

    relevant = _transition_ownership_relevant_receipts(
        intent,
        current=current_copy,
        receipts=receipts,
        include_prospective=True,
    )
    candidates = tuple(
        receipt
        for receipt in relevant
        if (
            receipt.receipt_id == intent.receipt_id
            or receipt.effect_id == intent.effect_id
            or receipt.operation_fence_id == intent.operation_fence_id
            or receipt.attempt_id == intent.attempt_id
        )
    )
    if candidates and any(receipt.intent != intent for receipt in candidates):
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_receipt_alias_conflict")
    distinct_candidates = {receipt.receipt_digest: receipt for receipt in candidates}
    if len(distinct_candidates) > 1:
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_receipt_alias_conflict")
    receipt = next(iter(distinct_candidates.values()), None)
    if any(candidate not in candidates for candidate in relevant):
        raise WorkflowTransitionOwnershipReservationConflict(
            "workflow_transition_ownership_prospective_receipt_conflict"
        )
    if receipt is not None and receipt.creator_claim_generation > generation:
        raise WorkflowTransitionOwnershipReservationConflict(
            "workflow_transition_ownership_receipt_generation_conflict"
        )
    current_receipts = tuple(
        candidate
        for candidate in relevant
        if current_copy is not None
        and (
            candidate.attempt_id == current_copy.attempt_id
            or (
                candidate.intent.tenant_id == current_copy.tenant_id
                and candidate.intent.run_id == current_copy.run_id
                and candidate.intent.step_id == current_copy.step_id
                and (
                    candidate.acquired_revision == current_copy.revision
                    or candidate.acquired_fencing_token == current_copy.fencing_token
                )
            )
        )
    )
    if receipt is None and current_receipts:
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_current_receipt_conflict")
    acquired_history: ExecutionOwnership | None = None
    if receipt is not None:
        acquired_matches = tuple(value for value in history_copy if value.revision == receipt.acquired_revision)
        if len(acquired_matches) != 1 or acquired_matches[0] != receipt.acquired_ownership:
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_acquired_history_conflict"
            )
        acquired_history = acquired_matches[0]
        if receipt.retry_consumption is not None:
            if retry_consumption != receipt.retry_consumption:
                raise WorkflowTransitionOwnershipReservationConflict(
                    "workflow_transition_ownership_retry_consumption_conflict"
                )
        elif retry_consumption is not None:
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_retry_consumption_unexpected"
            )
    else:
        if retry_consumption is not None:
            raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_retry_without_receipt")
        if current_copy is not None and (
            current_copy.attempt_id == intent.attempt_id or current_copy.owner_id == intent.owner_id
        ):
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_current_without_receipt"
            )
        if any(value.attempt_id == intent.attempt_id or value.owner_id == intent.owner_id for value in history_copy):
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_history_without_receipt"
            )

    if (
        retry_budget.tenant_id != intent.tenant_id
        or retry_budget.run_id != intent.run_id
        or retry_budget.maximum != intent.maximum_retries
        or retry_budget.used < 0
        or retry_budget.used > retry_budget.maximum
    ):
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_retry_budget_conflict")
    aliases = tuple(sorted({value.receipt_digest for value in relevant}))
    digest = _ownership_observation_digest(
        intent=intent,
        claim_generation=generation,
        current=current_copy,
        current_history=current_history,
        acquired_history=acquired_history,
        retry_consumption=retry_consumption,
        retry_budget=retry_budget,
        receipt=receipt,
        receipt_alias_digests=aliases,
    )
    return WorkflowTransitionOwnershipReservationObservation(
        intent=intent,
        claim_generation=generation,
        current=current_copy,
        current_history=current_history,
        acquired_history=acquired_history,
        retry_consumption=retry_consumption,
        retry_budget=retry_budget,
        receipt=receipt,
        receipt_alias_digests=aliases,
        observation_digest=digest,
    )


def _transition_ownership_evidence(
    intent: WorkflowTransitionOwnershipReservationIntent,
    *,
    history: tuple[ExecutionOwnership, ...],
    retry_consumption: WorkflowTransitionOwnershipRetryConsumption | None,
    receipts: tuple[WorkflowTransitionOwnershipReservationReceipt, ...],
) -> WorkflowTransitionOwnershipReservationEvidence:
    if not isinstance(intent, WorkflowTransitionOwnershipReservationIntent):
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_intent_invalid")
    candidates = tuple(
        receipt
        for receipt in receipts
        if (
            receipt.receipt_id == intent.receipt_id
            or receipt.effect_id == intent.effect_id
            or receipt.operation_fence_id == intent.operation_fence_id
            or receipt.attempt_id == intent.attempt_id
        )
    )
    distinct = {receipt.receipt_digest: receipt for receipt in candidates}
    if not distinct:
        if any(value.attempt_id == intent.attempt_id for value in history) or retry_consumption is not None:
            raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_evidence_partial")
        return WorkflowTransitionOwnershipReservationEvidence(
            intent=intent,
            receipt=None,
            prior_history=None,
            acquired_history=None,
            retry_consumption=None,
            receipt_alias_digests=(),
        )
    if len(distinct) != 1:
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_evidence_receipt_conflict")
    receipt = next(iter(distinct.values()))
    if receipt.intent != intent:
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_evidence_receipt_conflict")
    exact_history = tuple(ExecutionOwnership.from_exact_mapping(value.to_dict()) for value in history)
    acquired = tuple(
        value
        for value in exact_history
        if value.revision == receipt.acquired_revision and value.attempt_id == receipt.attempt_id
    )
    if len(acquired) != 1 or acquired[0] != receipt.acquired_ownership:
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_evidence_history_conflict")
    prior = (
        ()
        if receipt.prior_ownership is None
        else tuple(
            value
            for value in exact_history
            if value.revision == receipt.prior_ownership.revision
            and value.attempt_id == receipt.prior_ownership.attempt_id
        )
    )
    if receipt.prior_ownership is not None and (len(prior) != 1 or prior[0] != receipt.prior_ownership):
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_evidence_prior_conflict")
    return WorkflowTransitionOwnershipReservationEvidence(
        intent=intent,
        receipt=receipt,
        prior_history=prior[0] if prior else None,
        acquired_history=acquired[0],
        retry_consumption=retry_consumption,
        receipt_alias_digests=(receipt.receipt_digest,),
    )


def _transition_ownership_reservation_values(
    observation: WorkflowTransitionOwnershipReservationObservation,
    *,
    creator_claim_generation: int,
    expected_observation_digest: str,
    reserved_at: float,
) -> tuple[
    ExecutionOwnership,
    WorkflowTransitionOwnershipRetryConsumption | None,
    RetryBudgetSnapshot,
    WorkflowTransitionOwnershipReservationReceipt,
]:
    generation = _ownership_positive_integer(creator_claim_generation, "creator_claim_generation")
    expected = _ownership_sha256(expected_observation_digest, "expected_observation_digest")
    timestamp = _ownership_exact_timestamp(reserved_at, "reserved_at")
    if observation.claim_generation != generation:
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_observation_conflict")
    if observation.receipt is not None:
        if observation.receipt.creator_claim_generation > generation:
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_receipt_generation_conflict"
            )
        return (
            observation.receipt.acquired_ownership,
            observation.receipt.retry_consumption,
            observation.retry_budget,
            observation.receipt,
        )
    if observation.observation_digest != expected:
        raise WorkflowTransitionOwnershipReservationStale("workflow_transition_ownership_observation_stale")
    intent = observation.intent
    current = observation.current
    if timestamp < intent.planned_at:
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_reserved_at_invalid")
    if current is not None:
        if current.status == "completed":
            raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_completed_conflict")
        if current.status == "active" and current.lease_expires_at > timestamp:
            raise WorkflowTransitionOwnershipReservationHeld("workflow_transition_ownership_lease_held")
        if current.status not in {"active", "failed", "orphaned", "dead_letter"}:
            raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_takeover_invalid")
        if observation.current_history != current:
            raise WorkflowTransitionOwnershipReservationConflict(
                "workflow_transition_ownership_current_history_conflict"
            )
        if (
            current.revision >= _OWNERSHIP_MAX_LEGACY_REVISION
            or current.fencing_token >= _OWNERSHIP_MAX_LEGACY_REVISION
        ):
            raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_counter_exhausted")
    before = observation.retry_budget.used
    consumption = None
    after = before
    if current is not None:
        if before >= intent.maximum_retries:
            raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_retry_budget_exhausted")
        consumption = WorkflowTransitionOwnershipRetryConsumption(
            tenant_id=intent.tenant_id,
            run_id=intent.run_id,
            retry_id=intent.retry_id,
        )
        after = before + 1
    acquired = ExecutionOwnership(
        tenant_id=intent.tenant_id,
        workflow_id=intent.workflow_id,
        run_id=intent.run_id,
        step_id=intent.step_id,
        attempt_id=intent.attempt_id,
        owner_id=intent.owner_id,
        fencing_token=current.fencing_token + 1 if current is not None else 1,
        revision=current.revision + 1 if current is not None else 1,
        status="active",
        lease_expires_at=timestamp + intent.lease_seconds,
        last_heartbeat_at=timestamp,
    )
    if acquired.lease_expires_at <= timestamp:
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_lease_expiry_invalid")
    acquired = ExecutionOwnership.from_exact_mapping(acquired.to_dict())
    receipt = WorkflowTransitionOwnershipReservationReceipt.build(
        intent=intent,
        creator_claim_generation=generation,
        prior_ownership=current,
        acquired_ownership=acquired,
        retry_consumption=consumption,
        retry_budget_used_before=before,
        retry_budget_used_after=after,
        reserved_at=timestamp,
    )
    budget = RetryBudgetSnapshot(
        intent.tenant_id,
        intent.run_id,
        used=after,
        maximum=intent.maximum_retries,
    )
    return acquired, consumption, budget, receipt
