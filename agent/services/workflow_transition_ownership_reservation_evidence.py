"""Reservation evidence: observation states, receipts, results and proofs.

Package-internal building block of ``workflow_transition_ownership_reservation``.
These are pure, non-mutating checks over Hub ownership observations and
immutable reservation history.  The current lease check is a point-in-time
validity assertion and never a downstream execution capability.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol, runtime_checkable

from agent.services.workflow_runtime.ownership import (
    ExecutionOwnership,
    WorkflowTransitionOwnershipReservationEvidence,
    WorkflowTransitionOwnershipReservationHistoricalReadPort,
    WorkflowTransitionOwnershipReservationIntent,
    WorkflowTransitionOwnershipReservationObservation,
    WorkflowTransitionOwnershipReservationReadPort,
    WorkflowTransitionOwnershipReservationReceipt,
)
from agent.services.workflow_transition_effect_proofs import (
    WorkflowTransitionEffectAbsenceProof,
    WorkflowTransitionEffectProofContext,
    WorkflowTransitionEffectResourceProof,
    assert_active_workflow_transition_effect_absence_proof_binding,
    assert_durable_workflow_transition_effect_proof_binding,
)
from agent.services.workflow_transition_outbox import (
    WorkflowTransition,
    WorkflowTransitionEffect,
    thaw_json,
    workflow_transition_effect_stage_attempt_count,
)
from agent.services.workflow_transition_ownership_reservation_staging import (
    _MAX_OWNERSHIP_COUNTER,
    WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_RESOURCE_KIND,
    WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_RESULT_SCHEMA,
    WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_SLOT_KIND,
    WorkflowTransitionOwnershipReservationError,
    _clock_value,
    _intent_from_effect,
    _sha256,
)

_RESULT_FIELDS = frozenset({"schema", "receipt"})


@runtime_checkable
class WorkflowTransitionOwnershipReservationObserverReads(
    WorkflowTransitionOwnershipReservationReadPort,
    WorkflowTransitionOwnershipReservationHistoricalReadPort,
    Protocol,
):
    """Current observation plus current-independent historical evidence."""


def assert_durable_workflow_transition_ownership_reservation_proof(
    *,
    transition: WorkflowTransition,
    effect: WorkflowTransitionEffect,
    reads: WorkflowTransitionOwnershipReservationHistoricalReadPort,
) -> WorkflowTransitionEffectResourceProof:
    """Validate persisted result/proof against receipt and immutable history."""

    proof_raw, result_raw = _persisted_evidence(effect)
    intent = _intent_from_effect(effect, transition=transition)
    try:
        evidence = reads.read_transition_reservation_history(intent)
    except Exception as exc:
        raise WorkflowTransitionOwnershipReservationError(
            "workflow_transition_ownership_reservation_durable_read_invalid"
        ) from exc
    receipt = _assert_historical_evidence(
        evidence,
        intent=intent,
        claim_generation=effect.applied_generation,
    )
    _assert_result(result_raw, receipt)
    return assert_durable_workflow_transition_effect_proof_binding(
        proof_raw,
        transition=transition,
        effect=effect,
        resource_kind=WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_RESOURCE_KIND,
        resource_id=receipt.receipt_id,
        resource_revision=1,
        resource_digest=receipt.receipt_digest,
    )


def assert_current_workflow_transition_ownership_reservation_validity(
    *,
    transition: WorkflowTransition,
    effect: WorkflowTransitionEffect,
    reads: WorkflowTransitionOwnershipReservationObserverReads,
    clock: Callable[[], float],
) -> None:
    """Assert a point-in-time current lease; return no authority capability."""

    proof_raw, result_raw = _persisted_evidence(effect)
    intent = _intent_from_effect(effect, transition=transition)
    try:
        evidence = reads.read_transition_reservation_history(intent)
    except Exception as exc:
        raise WorkflowTransitionOwnershipReservationError(
            "workflow_transition_ownership_reservation_current_history_invalid"
        ) from exc
    durable_receipt = _assert_historical_evidence(
        evidence,
        intent=intent,
        claim_generation=effect.applied_generation,
    )
    _assert_result(result_raw, durable_receipt)
    assert_durable_workflow_transition_effect_proof_binding(
        proof_raw,
        transition=transition,
        effect=effect,
        resource_kind=WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_RESOURCE_KIND,
        resource_id=durable_receipt.receipt_id,
        resource_revision=1,
        resource_digest=durable_receipt.receipt_digest,
    )
    try:
        snapshot = reads.observe_transition_reservation(
            intent,
            claim_generation=effect.applied_generation,
        )
    except Exception as exc:
        raise WorkflowTransitionOwnershipReservationError(
            "workflow_transition_ownership_reservation_current_read_invalid"
        ) from exc
    current_receipt = _assert_durable_snapshot(
        snapshot,
        intent=intent,
        claim_generation=effect.applied_generation,
    )
    if current_receipt != durable_receipt:
        raise WorkflowTransitionOwnershipReservationError(
            "workflow_transition_ownership_reservation_current_history_conflict"
        )
    _assert_active_current(
        snapshot,
        receipt=durable_receipt,
        now=_clock_value(clock),
    )
    return None


def workflow_transition_ownership_reservation_receipt_from_result(
    result: Mapping[str, Any],
) -> WorkflowTransitionOwnershipReservationReceipt:
    if not isinstance(result, Mapping) or set(result) != _RESULT_FIELDS:
        raise WorkflowTransitionOwnershipReservationError("workflow_transition_ownership_reservation_result_invalid")
    if result["schema"] != WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_RESULT_SCHEMA:
        raise WorkflowTransitionOwnershipReservationError(
            "workflow_transition_ownership_reservation_result_schema_unsupported"
        )
    try:
        raw_receipt = result["receipt"]
        if not isinstance(raw_receipt, Mapping):
            raise TypeError("receipt")
        return WorkflowTransitionOwnershipReservationReceipt.from_mapping(raw_receipt)
    except Exception as exc:
        raise WorkflowTransitionOwnershipReservationError(
            "workflow_transition_ownership_reservation_result_invalid"
        ) from exc


def _observation_state(
    snapshot: WorkflowTransitionOwnershipReservationObservation,
    *,
    intent: WorkflowTransitionOwnershipReservationIntent,
    claim_generation: int,
    now: float,
) -> str:
    _assert_observation_projection(
        snapshot,
        intent=intent,
        claim_generation=claim_generation,
    )
    if snapshot.receipt is not None:
        _assert_durable_snapshot(
            snapshot,
            intent=intent,
            claim_generation=claim_generation,
        )
        return "receipt"
    if snapshot.receipt_alias_digests or snapshot.acquired_history is not None:
        return "conflict"
    if snapshot.retry_consumption is not None:
        return "conflict"
    current = snapshot.current
    if current is None:
        return "executable" if snapshot.current_history is None else "conflict"
    if snapshot.current_history != current or not _ownership_scope_matches(current, intent):
        return "conflict"
    if current.attempt_id == intent.attempt_id or current.owner_id == intent.owner_id:
        return "conflict"
    if current.status == "completed":
        return "conflict"
    if current.revision >= _MAX_OWNERSHIP_COUNTER or current.fencing_token >= _MAX_OWNERSHIP_COUNTER:
        return "counter_exhausted"
    if snapshot.retry_budget.used >= snapshot.retry_budget.maximum:
        return "retry_exhausted"
    if current.status == "active":
        return "held" if current.lease_expires_at > now else "executable"
    if current.status in {"failed", "orphaned", "dead_letter"}:
        return "executable"
    return "conflict"


def _assert_observation_projection(
    snapshot: WorkflowTransitionOwnershipReservationObservation,
    *,
    intent: WorkflowTransitionOwnershipReservationIntent,
    claim_generation: int,
) -> None:
    if (
        type(snapshot) is not WorkflowTransitionOwnershipReservationObservation
        or snapshot.intent != intent
        or snapshot.claim_generation != claim_generation
        or snapshot.retry_budget.tenant_id != intent.tenant_id
        or snapshot.retry_budget.run_id != intent.run_id
        or snapshot.retry_budget.maximum != intent.maximum_retries
        or snapshot.retry_budget.used < 0
        or snapshot.retry_budget.used > snapshot.retry_budget.maximum
    ):
        raise WorkflowTransitionOwnershipReservationError(
            "workflow_transition_ownership_reservation_observation_projection_invalid"
        )
    _sha256(snapshot.observation_digest, "observation_digest")
    for digest in snapshot.receipt_alias_digests:
        _sha256(digest, "receipt_alias_digest")


def _assert_durable_snapshot(
    snapshot: WorkflowTransitionOwnershipReservationObservation,
    *,
    intent: WorkflowTransitionOwnershipReservationIntent,
    claim_generation: int,
) -> WorkflowTransitionOwnershipReservationReceipt:
    _assert_observation_projection(
        snapshot,
        intent=intent,
        claim_generation=claim_generation,
    )
    receipt = _required_receipt(snapshot)
    if (
        receipt.intent != intent
        or receipt.creator_claim_generation > claim_generation
        or snapshot.acquired_history != receipt.acquired_ownership
        or snapshot.retry_consumption != receipt.retry_consumption
        or snapshot.retry_budget.used < receipt.retry_budget_used_after
        or not snapshot.receipt_alias_digests
        or set(snapshot.receipt_alias_digests) != {receipt.receipt_digest}
    ):
        raise WorkflowTransitionOwnershipReservationError(
            "workflow_transition_ownership_reservation_durable_projection_invalid"
        )
    return receipt


def _assert_historical_evidence(
    evidence: WorkflowTransitionOwnershipReservationEvidence,
    *,
    intent: WorkflowTransitionOwnershipReservationIntent,
    claim_generation: int,
) -> WorkflowTransitionOwnershipReservationReceipt:
    if (
        type(evidence) is not WorkflowTransitionOwnershipReservationEvidence
        or evidence.intent != intent
        or evidence.receipt is None
    ):
        raise WorkflowTransitionOwnershipReservationError("workflow_transition_ownership_reservation_history_invalid")
    receipt = evidence.receipt
    if (
        receipt.intent != intent
        or receipt.creator_claim_generation > claim_generation
        or evidence.prior_history != receipt.prior_ownership
        or evidence.acquired_history != receipt.acquired_ownership
        or evidence.retry_consumption != receipt.retry_consumption
        or evidence.receipt_alias_digests != (receipt.receipt_digest,)
    ):
        raise WorkflowTransitionOwnershipReservationError("workflow_transition_ownership_reservation_history_conflict")
    return receipt


def _assert_active_current(
    snapshot: WorkflowTransitionOwnershipReservationObservation,
    *,
    receipt: WorkflowTransitionOwnershipReservationReceipt,
    now: float,
) -> None:
    current = snapshot.current
    acquired = receipt.acquired_ownership
    if (
        current is None
        or snapshot.current_history != current
        or not _ownership_scope_matches(current, receipt.intent)
        or current.attempt_id != acquired.attempt_id
        or current.owner_id != acquired.owner_id
        or current.fencing_token != acquired.fencing_token
        or current.revision < acquired.revision
        or current.status != "active"
        or current.lease_expires_at <= now
        or current.result_ack_key
        or current.failure_code
    ):
        raise WorkflowTransitionOwnershipReservationError("workflow_transition_ownership_reservation_current_invalid")


def _ownership_scope_matches(
    ownership: ExecutionOwnership,
    intent: WorkflowTransitionOwnershipReservationIntent,
) -> bool:
    return (
        ownership.tenant_id == intent.tenant_id
        and ownership.workflow_id == intent.workflow_id
        and ownership.run_id == intent.run_id
        and ownership.step_id == intent.step_id
    )


def _absence_proof(
    *,
    transition: WorkflowTransition,
    effect: WorkflowTransitionEffect,
    claim_generation: int,
    snapshot: WorkflowTransitionOwnershipReservationObservation,
) -> WorkflowTransitionEffectAbsenceProof:
    return WorkflowTransitionEffectAbsenceProof(
        context=WorkflowTransitionEffectProofContext.from_active_claim(
            transition=transition,
            effect=effect,
            claim_generation=claim_generation,
        ),
        resource_kind=WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_SLOT_KIND,
        resource_id=snapshot.intent.receipt_id,
        head_revision=snapshot.current.revision if snapshot.current is not None else 0,
        head_digest=snapshot.observation_digest,
    )


def _assert_absence_proof(
    proof: Mapping[str, Any],
    *,
    transition: WorkflowTransition,
    effect: WorkflowTransitionEffect,
    claim_generation: int,
    snapshot: WorkflowTransitionOwnershipReservationObservation,
) -> None:
    assert_active_workflow_transition_effect_absence_proof_binding(
        proof,
        transition=transition,
        effect=effect,
        claim_generation=claim_generation,
        resource_kind=WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_SLOT_KIND,
        resource_id=snapshot.intent.receipt_id,
        head_revision=snapshot.current.revision if snapshot.current is not None else 0,
        head_digest=snapshot.observation_digest,
    )


def _resource_proof(
    *,
    transition: WorkflowTransition,
    effect: WorkflowTransitionEffect,
    claim_generation: int,
    receipt: WorkflowTransitionOwnershipReservationReceipt,
) -> WorkflowTransitionEffectResourceProof:
    if receipt.creator_claim_generation > claim_generation:
        raise WorkflowTransitionOwnershipReservationError(
            "workflow_transition_ownership_reservation_generation_conflict"
        )
    return WorkflowTransitionEffectResourceProof(
        context=WorkflowTransitionEffectProofContext.from_active_claim(
            transition=transition,
            effect=effect,
            claim_generation=claim_generation,
        ),
        resource_kind=WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_RESOURCE_KIND,
        resource_id=receipt.receipt_id,
        resource_revision=1,
        resource_digest=receipt.receipt_digest,
    )


def _result(
    receipt: WorkflowTransitionOwnershipReservationReceipt,
) -> dict[str, object]:
    return {
        "schema": WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_RESULT_SCHEMA,
        "receipt": receipt.to_dict(),
    }


def _assert_result(
    result: Mapping[str, Any],
    receipt: WorkflowTransitionOwnershipReservationReceipt,
) -> None:
    parsed = workflow_transition_ownership_reservation_receipt_from_result(result)
    if parsed != receipt:
        raise WorkflowTransitionOwnershipReservationError("workflow_transition_ownership_reservation_result_conflict")


def _required_receipt(
    snapshot: WorkflowTransitionOwnershipReservationObservation,
) -> WorkflowTransitionOwnershipReservationReceipt:
    if snapshot.receipt is None:
        raise WorkflowTransitionOwnershipReservationError("workflow_transition_ownership_reservation_receipt_missing")
    return snapshot.receipt


def _persisted_evidence(
    effect: WorkflowTransitionEffect,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    try:
        workflow_transition_effect_stage_attempt_count(effect.result_payload)
        envelope = thaw_json(effect.result_payload)
        result = envelope["effect_result"]
        proof = envelope["effect_proof"]
        if not isinstance(result, Mapping) or not isinstance(proof, Mapping):
            raise TypeError("persisted evidence")
        return proof, result
    except Exception as exc:
        raise WorkflowTransitionOwnershipReservationError(
            "workflow_transition_ownership_reservation_persisted_proof_invalid"
        ) from exc


__all__ = [
    "WorkflowTransitionOwnershipReservationObserverReads",
    "assert_current_workflow_transition_ownership_reservation_validity",
    "assert_durable_workflow_transition_ownership_reservation_proof",
    "workflow_transition_ownership_reservation_receipt_from_result",
]
