"""Staged execution-reservation effect: schema, deterministic IDs and parsing.

Package-internal building block of ``workflow_transition_ownership_reservation``.
It builds the generation-stable staged effect and parses it back into the
exact Hub ownership reservation intent, failing closed with stable
``workflow_transition_ownership_reservation_*`` reason codes.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import final

from agent.services.workflow_runtime.ownership import (
    WorkflowTransitionOwnershipReservationIntent,
    workflow_transition_ownership_attempt_id,
    workflow_transition_ownership_intent_digest,
    workflow_transition_ownership_operation_fence_id,
    workflow_transition_ownership_owner_id,
    workflow_transition_ownership_receipt_id,
)
from agent.services.workflow_transition_effect_proofs import WorkflowTransitionEffectScalars
from agent.services.workflow_transition_outbox import (
    EFFECT_OWNERSHIP_RESERVE,
    TRANSITION_RUNTIMES,
    WorkflowTransition,
    WorkflowTransitionEffect,
    workflow_transition_effect_id,
)

WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_EFFECT_SCHEMA = "ananta.workflow_transition_ownership_reservation_effect.v1"
WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_RESULT_SCHEMA = "ananta.workflow_transition_ownership_reservation_result.v1"
WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_RESOURCE_KIND = "workflow_execution_ownership_reservation_receipt"
WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_SLOT_KIND = "workflow_execution_ownership_reservation_slot"

_EFFECT_FIELDS = frozenset(
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
        "ownership_intent_digest",
        "owner_id",
        "operation_fence_id",
        "attempt_id",
        "retry_id",
        "lease_seconds",
        "maximum_retries",
    }
)
_MAX_DOMAIN_INTEGER = 2**63 - 1
_MAX_OWNERSHIP_COUNTER = 2_147_483_647
_MAXIMUM_RETRIES = 2_147_483_647


class WorkflowTransitionOwnershipReservationError(ValueError):
    """Stable fail-closed effect, result, or proof binding error."""


_SCALARS = WorkflowTransitionEffectScalars(
    error=WorkflowTransitionOwnershipReservationError,
    prefix="workflow_transition_ownership_reservation",
)


@final
@dataclass(frozen=True, slots=True)
class _StagedOwnershipReservation:
    receipt_id: str
    transition_id: str
    effect_id: str
    runtime_id: str
    tenant_id: str
    workflow_id: str
    run_id: str
    step_id: str
    effect_ordinal: int
    ownership_intent_digest: str
    owner_id: str
    operation_fence_id: str
    attempt_id: str
    retry_id: str
    lease_seconds: float
    maximum_retries: int


def build_workflow_transition_ownership_reservation_effect(
    *,
    transition_id: str,
    tenant_id: str,
    workflow_id: str,
    run_id: str,
    runtime_id: str,
    ordinal: int,
    step_id: str,
    lease_seconds: float,
    maximum_retries: int,
    planned_at: float,
) -> WorkflowTransitionEffect:
    """Build generation-stable owner, operation-fence, attempt, and receipt IDs."""

    try:
        transition = _identity(transition_id, "transition_id")
        tenant = _identity(tenant_id, "tenant_id")
        workflow = _identity(workflow_id, "workflow_id")
        run = _identity(run_id, "run_id")
        runtime = _identity(runtime_id, "runtime_id")
        if runtime not in TRANSITION_RUNTIMES:
            raise WorkflowTransitionOwnershipReservationError(
                "workflow_transition_ownership_reservation_runtime_invalid"
            )
        position = _positive_integer(ordinal, "ordinal")
        step = _identity(step_id, "step_id")
        lease = _positive_float(lease_seconds, "lease_seconds")
        maximum = _maximum_retries(maximum_retries)
        timestamp = _positive_float(planned_at, "planned_at")
        _finite_lease_end(timestamp, lease)
        intent_digest = workflow_transition_ownership_intent_digest(
            transition_id=transition,
            runtime_id=runtime,
            tenant_id=tenant,
            workflow_id=workflow,
            run_id=run,
            step_id=step,
            effect_ordinal=position,
            lease_seconds=lease,
            maximum_retries=maximum,
        )
        owner_id = workflow_transition_ownership_owner_id(ownership_intent_digest=intent_digest)
        operation_fence_id = workflow_transition_ownership_operation_fence_id(
            ownership_intent_digest=intent_digest,
            owner_id=owner_id,
        )
        effect_id = workflow_transition_effect_id(
            transition_id=transition,
            ordinal=position,
            kind=EFFECT_OWNERSHIP_RESERVE,
            idempotency_key=operation_fence_id,
        )
        attempt_id = workflow_transition_ownership_attempt_id(
            effect_id=effect_id,
            operation_fence_id=operation_fence_id,
        )
        receipt_id = workflow_transition_ownership_receipt_id(
            transition_id=transition,
            effect_id=effect_id,
        )
        payload: dict[str, object] = {
            "schema": WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_EFFECT_SCHEMA,
            "receipt_id": receipt_id,
            "transition_id": transition,
            "effect_id": effect_id,
            "runtime_id": runtime,
            "tenant_id": tenant,
            "workflow_id": workflow,
            "run_id": run,
            "step_id": step,
            "effect_ordinal": position,
            "ownership_intent_digest": intent_digest,
            "owner_id": owner_id,
            "operation_fence_id": operation_fence_id,
            "attempt_id": attempt_id,
            "retry_id": operation_fence_id,
            "lease_seconds": lease,
            "maximum_retries": maximum,
        }
        effect = WorkflowTransitionEffect.build(
            transition_id=transition,
            ordinal=position,
            kind=EFFECT_OWNERSHIP_RESERVE,
            idempotency_key=operation_fence_id,
            payload=payload,
            created_at=timestamp,
        )
        _staged_reservation(effect)
        return effect
    except WorkflowTransitionOwnershipReservationError:
        raise
    except Exception as exc:
        raise WorkflowTransitionOwnershipReservationError(
            "workflow_transition_ownership_reservation_payload_invalid"
        ) from exc


def _intent_from_effect(
    effect: WorkflowTransitionEffect,
    *,
    transition: WorkflowTransition,
) -> WorkflowTransitionOwnershipReservationIntent:
    staged = _staged_reservation(effect)
    if (
        not isinstance(transition, WorkflowTransition)
        or transition.transition_id != staged.transition_id
        or transition.runtime_id != staged.runtime_id
        or transition.tenant_id != staged.tenant_id
        or transition.workflow_id != staged.workflow_id
        or transition.run_id != staged.run_id
        or transition.created_at != effect.created_at
    ):
        raise WorkflowTransitionOwnershipReservationError(
            "workflow_transition_ownership_reservation_transition_binding_invalid"
        )
    try:
        return WorkflowTransitionOwnershipReservationIntent(
            receipt_id=staged.receipt_id,
            transition_id=staged.transition_id,
            effect_id=staged.effect_id,
            runtime_id=staged.runtime_id,
            tenant_id=staged.tenant_id,
            workflow_id=staged.workflow_id,
            run_id=staged.run_id,
            step_id=staged.step_id,
            effect_ordinal=staged.effect_ordinal,
            ownership_intent_digest=staged.ownership_intent_digest,
            owner_id=staged.owner_id,
            operation_fence_id=staged.operation_fence_id,
            attempt_id=staged.attempt_id,
            retry_id=staged.retry_id,
            transition_request_fingerprint=transition.request_fingerprint,
            effect_payload_digest=effect.payload_digest,
            idempotency_key=effect.idempotency_key,
            lease_seconds=staged.lease_seconds,
            maximum_retries=staged.maximum_retries,
            planned_at=effect.created_at,
        )
    except Exception as exc:
        raise WorkflowTransitionOwnershipReservationError(
            "workflow_transition_ownership_reservation_intent_invalid"
        ) from exc


def _staged_reservation(effect: WorkflowTransitionEffect) -> _StagedOwnershipReservation:
    if not isinstance(effect, WorkflowTransitionEffect) or effect.kind != EFFECT_OWNERSHIP_RESERVE:
        raise WorkflowTransitionOwnershipReservationError("workflow_transition_ownership_reservation_effect_invalid")
    raw = effect.payload
    if not isinstance(raw, Mapping) or set(raw) != _EFFECT_FIELDS:
        raise WorkflowTransitionOwnershipReservationError("workflow_transition_ownership_reservation_payload_invalid")
    try:
        staged = _StagedOwnershipReservation(
            receipt_id=_identity(raw["receipt_id"], "receipt_id"),
            transition_id=_identity(raw["transition_id"], "transition_id"),
            effect_id=_identity(raw["effect_id"], "effect_id"),
            runtime_id=_identity(raw["runtime_id"], "runtime_id"),
            tenant_id=_identity(raw["tenant_id"], "tenant_id"),
            workflow_id=_identity(raw["workflow_id"], "workflow_id"),
            run_id=_identity(raw["run_id"], "run_id"),
            step_id=_identity(raw["step_id"], "step_id"),
            effect_ordinal=_positive_integer(raw["effect_ordinal"], "effect_ordinal"),
            ownership_intent_digest=_sha256(raw["ownership_intent_digest"], "ownership_intent_digest"),
            owner_id=_identity(raw["owner_id"], "owner_id"),
            operation_fence_id=_identity(raw["operation_fence_id"], "operation_fence_id"),
            attempt_id=_identity(raw["attempt_id"], "attempt_id"),
            retry_id=_identity(raw["retry_id"], "retry_id"),
            lease_seconds=_positive_float(raw["lease_seconds"], "lease_seconds"),
            maximum_retries=_maximum_retries(raw["maximum_retries"]),
        )
    except Exception as exc:
        raise WorkflowTransitionOwnershipReservationError(
            "workflow_transition_ownership_reservation_payload_invalid"
        ) from exc
    expected_digest = workflow_transition_ownership_intent_digest(
        transition_id=staged.transition_id,
        runtime_id=staged.runtime_id,
        tenant_id=staged.tenant_id,
        workflow_id=staged.workflow_id,
        run_id=staged.run_id,
        step_id=staged.step_id,
        effect_ordinal=staged.effect_ordinal,
        lease_seconds=staged.lease_seconds,
        maximum_retries=staged.maximum_retries,
    )
    expected_owner = workflow_transition_ownership_owner_id(ownership_intent_digest=expected_digest)
    expected_fence = workflow_transition_ownership_operation_fence_id(
        ownership_intent_digest=expected_digest,
        owner_id=expected_owner,
    )
    expected_effect = workflow_transition_effect_id(
        transition_id=staged.transition_id,
        ordinal=staged.effect_ordinal,
        kind=EFFECT_OWNERSHIP_RESERVE,
        idempotency_key=expected_fence,
    )
    expected_attempt = workflow_transition_ownership_attempt_id(
        effect_id=expected_effect,
        operation_fence_id=expected_fence,
    )
    expected_receipt = workflow_transition_ownership_receipt_id(
        transition_id=staged.transition_id,
        effect_id=expected_effect,
    )
    if (
        raw["schema"] != WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_EFFECT_SCHEMA
        or staged.runtime_id not in TRANSITION_RUNTIMES
        or staged.ownership_intent_digest != expected_digest
        or staged.owner_id != expected_owner
        or staged.operation_fence_id != expected_fence
        or staged.retry_id != expected_fence
        or staged.effect_id != expected_effect
        or staged.effect_id != effect.effect_id
        or staged.attempt_id != expected_attempt
        or staged.receipt_id != expected_receipt
        or staged.transition_id != effect.transition_id
        or staged.effect_ordinal != effect.ordinal
        or effect.idempotency_key != expected_fence
    ):
        raise WorkflowTransitionOwnershipReservationError(
            "workflow_transition_ownership_reservation_payload_binding_invalid"
        )
    _finite_lease_end(_positive_float(effect.created_at, "planned_at"), staged.lease_seconds)
    return staged


def _clock_value(clock: Callable[[], float]) -> float:
    if not callable(clock):
        raise WorkflowTransitionOwnershipReservationError("workflow_transition_ownership_reservation_clock_invalid")
    return _positive_float(clock(), "clock")


def _reservation_clock_value(
    clock: Callable[[], float],
    *,
    intent: WorkflowTransitionOwnershipReservationIntent,
) -> float:
    reserved_at = _clock_value(clock)
    if reserved_at < intent.planned_at:
        raise WorkflowTransitionOwnershipReservationError("workflow_transition_ownership_reservation_clock_invalid")
    _finite_lease_end(reserved_at, intent.lease_seconds)
    return reserved_at


def _identity(value: object, reason: str) -> str:
    return _SCALARS.identity(value, reason)


def _sha256(value: object, reason: str) -> str:
    return _SCALARS.sha256(value, reason)


def _positive_integer(value: object, reason: str) -> int:
    return _SCALARS.positive_integer(value, reason, maximum=_MAX_DOMAIN_INTEGER)


def _maximum_retries(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _MAXIMUM_RETRIES:
        raise WorkflowTransitionOwnershipReservationError(
            "workflow_transition_ownership_reservation_maximum_retries_invalid"
        )
    return value


def _positive_float(value: object, reason: str) -> float:
    if type(value) is not float or not math.isfinite(value) or value <= 0:
        raise WorkflowTransitionOwnershipReservationError(f"workflow_transition_ownership_reservation_{reason}_invalid")
    return value


def _finite_lease_end(planned_at: float, lease_seconds: float) -> float:
    lease_end = planned_at + lease_seconds
    if not math.isfinite(lease_end) or lease_end <= planned_at:
        raise WorkflowTransitionOwnershipReservationError("workflow_transition_ownership_reservation_lease_end_invalid")
    return lease_end


__all__ = [
    "WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_EFFECT_SCHEMA",
    "WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_RESOURCE_KIND",
    "WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_RESULT_SCHEMA",
    "WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_SLOT_KIND",
    "WorkflowTransitionOwnershipReservationError",
    "build_workflow_transition_ownership_reservation_effect",
]
