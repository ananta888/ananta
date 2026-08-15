"""Hub-owned task-queue reservation transition effect.

The effect reserves exactly one queue slot for one transition effect and
records an immutable receipt.  It deliberately does not create, claim or
mutate the task itself: the queue service remains the sole owner of task
state, and this receipt only proves which effect was permitted to take one
slot.  A restart therefore adopts the existing reservation instead of taking
a second slot, which is what makes an interrupted run converge on one task.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, final, runtime_checkable

from agent.services.workflow_runtime.queue_reservations import (
    WorkflowTransitionQueueReservationAuthority,
    WorkflowTransitionQueueReservationConflict,
    WorkflowTransitionQueueReservationIntent,
    WorkflowTransitionQueueReservationReadPort,
    WorkflowTransitionQueueReservationReceipt,
    WorkflowTransitionQueueReservationUnavailable,
    workflow_transition_queue_attempt_id,
    workflow_transition_queue_intent_digest,
    workflow_transition_queue_operation_fence_id,
    workflow_transition_queue_receipt_id,
)
from agent.services.workflow_transition_effect_execution import (
    EffectAlreadyApplied,
    EffectApplied,
    EffectExecutable,
    EffectQuarantine,
    EffectRetry,
    WorkflowTransitionEffectAttempt,
    WorkflowTransitionEffectObservation,
    WorkflowTransitionHeartbeatContext,
)
from agent.services.workflow_transition_effect_proofs import (
    WorkflowTransitionEffectAbsenceProof,
    WorkflowTransitionEffectProofContext,
    WorkflowTransitionEffectResourceProof,
    WorkflowTransitionEffectScalars,
    workflow_transition_effect_resource_digest,
)
from agent.services.workflow_transition_outbox import (
    EFFECT_QUEUE_RESERVE,
    TRANSITION_RUNTIMES,
    WorkflowTransition,
    WorkflowTransitionEffect,
    thaw_json,
    workflow_transition_effect_id,
)

WORKFLOW_TRANSITION_QUEUE_RESERVATION_EFFECT_SCHEMA = "ananta.workflow_transition_queue_reservation_effect.v1"
WORKFLOW_TRANSITION_QUEUE_RESERVATION_RESULT_SCHEMA = "ananta.workflow_transition_queue_reservation_result.v1"
WORKFLOW_TRANSITION_QUEUE_RESERVATION_RESOURCE_KIND = "workflow_queue_reservation_receipt"
WORKFLOW_TRANSITION_QUEUE_RESERVATION_SLOT_KIND = "workflow_queue_reservation_slot"

_EFFECT_PAYLOAD_FIELDS = frozenset(
    {
        "schema",
        "transition_id",
        "effect_id",
        "runtime_id",
        "tenant_id",
        "workflow_id",
        "run_id",
        "step_id",
        "task_id",
        "effect_ordinal",
        "queue_intent_digest",
        "operation_fence_id",
        "attempt_id",
        "receipt_id",
        "maximum_retries",
    }
)
_MAX_DOMAIN_INTEGER = 2**63 - 1


class WorkflowTransitionQueueReservationEffectError(ValueError):
    """Stable fail-closed effect, result, or proof binding error."""


_SCALARS = WorkflowTransitionEffectScalars(
    error=WorkflowTransitionQueueReservationEffectError,
    prefix="workflow_transition_queue_reservation_effect",
)


@runtime_checkable
class WorkflowTransitionQueueReservationObserverReads(
    WorkflowTransitionQueueReservationReadPort,
    Protocol,
):
    """Current observation of one exact reservation slot."""


@final
@dataclass(frozen=True, slots=True)
class _StagedQueueReservation:
    transition_id: str
    effect_id: str
    runtime_id: str
    tenant_id: str
    workflow_id: str
    run_id: str
    step_id: str
    task_id: str
    effect_ordinal: int
    queue_intent_digest: str
    operation_fence_id: str
    attempt_id: str
    receipt_id: str
    maximum_retries: int


def build_workflow_transition_queue_reservation_effect(
    *,
    transition_id: str,
    tenant_id: str,
    workflow_id: str,
    run_id: str,
    runtime_id: str,
    ordinal: int,
    step_id: str,
    task_id: str,
    maximum_retries: int,
    planned_at: float,
) -> WorkflowTransitionEffect:
    """Build a byte-deterministic queue reservation intent for one task."""

    try:
        transition = _identity(transition_id, "transition_id")
        tenant = _identity(tenant_id, "tenant_id")
        workflow = _identity(workflow_id, "workflow_id")
        run = _identity(run_id, "run_id")
        runtime = _identity(runtime_id, "runtime_id")
        if runtime not in TRANSITION_RUNTIMES:
            raise WorkflowTransitionQueueReservationEffectError(
                "workflow_transition_queue_reservation_effect_runtime_invalid"
            )
        position = _positive_integer(ordinal, "ordinal")
        step = _identity(step_id, "step_id")
        task = _identity(task_id, "task_id")
        maximum = _maximum_retries(maximum_retries)
        timestamp = _positive_float(planned_at, "planned_at")
        queue_intent_digest = workflow_transition_queue_intent_digest(
            transition_id=transition,
            runtime_id=runtime,
            tenant_id=tenant,
            workflow_id=workflow,
            run_id=run,
            step_id=step,
            task_id=task,
            effect_ordinal=position,
            maximum_retries=maximum,
        )
        operation_fence_id = workflow_transition_queue_operation_fence_id(
            queue_intent_digest=queue_intent_digest,
        )
        effect_id = workflow_transition_effect_id(
            transition_id=transition,
            ordinal=position,
            kind=EFFECT_QUEUE_RESERVE,
            idempotency_key=operation_fence_id,
        )
        payload = {
            "schema": WORKFLOW_TRANSITION_QUEUE_RESERVATION_EFFECT_SCHEMA,
            "transition_id": transition,
            "effect_id": effect_id,
            "runtime_id": runtime,
            "tenant_id": tenant,
            "workflow_id": workflow,
            "run_id": run,
            "step_id": step,
            "task_id": task,
            "effect_ordinal": position,
            "queue_intent_digest": queue_intent_digest,
            "operation_fence_id": operation_fence_id,
            "attempt_id": workflow_transition_queue_attempt_id(
                effect_id=effect_id,
                operation_fence_id=operation_fence_id,
            ),
            "receipt_id": workflow_transition_queue_receipt_id(
                transition_id=transition,
                effect_id=effect_id,
            ),
            "maximum_retries": maximum,
        }
        return WorkflowTransitionEffect.build(
            transition_id=transition,
            ordinal=position,
            kind=EFFECT_QUEUE_RESERVE,
            idempotency_key=operation_fence_id,
            payload=payload,
            created_at=timestamp,
        )
    except WorkflowTransitionQueueReservationEffectError:
        raise
    except Exception as exc:
        raise WorkflowTransitionQueueReservationEffectError(
            "workflow_transition_queue_reservation_effect_invalid"
        ) from exc


@final
class WorkflowTransitionQueueReservationObserver:
    """Observe exact reservation state without consuming a retry budget."""

    __slots__ = ("_reads",)

    def __init__(self, *, reads: WorkflowTransitionQueueReservationObserverReads) -> None:
        if not isinstance(reads, WorkflowTransitionQueueReservationObserverReads):
            raise WorkflowTransitionQueueReservationEffectError(
                "workflow_transition_queue_reservation_effect_observer_invalid"
            )
        self._reads = reads

    def observe_or_adopt(
        self,
        observation: WorkflowTransitionEffectObservation,
        *,
        heartbeat: WorkflowTransitionHeartbeatContext,
    ) -> EffectAlreadyApplied | EffectExecutable | EffectQuarantine | EffectRetry:
        del heartbeat
        try:
            transition, effect, generation, staged = _staged_from_observation(observation)
        except Exception:
            return EffectQuarantine("queue_reservation_observation_invalid")
        try:
            snapshot = self._reads.observe_transition_queue_reservation(
                tenant_id=staged.tenant_id,
                run_id=staged.run_id,
                effect_id=staged.effect_id,
            )
        except WorkflowTransitionQueueReservationUnavailable:
            return EffectRetry("queue_reservation_unavailable")
        except Exception:
            return EffectQuarantine("queue_reservation_observation_failed")
        if snapshot.receipt is not None:
            if snapshot.receipt.operation_fence_id != staged.operation_fence_id:
                return EffectQuarantine("queue_reservation_fence_conflict")
            return _already_applied(
                transition=transition,
                effect=effect,
                claim_generation=generation,
                receipt=snapshot.receipt,
            )
        proof = WorkflowTransitionEffectAbsenceProof(
            context=WorkflowTransitionEffectProofContext.from_active_claim(
                transition=transition,
                effect=effect,
                claim_generation=generation,
            ),
            resource_kind=WORKFLOW_TRANSITION_QUEUE_RESERVATION_SLOT_KIND,
            resource_id=staged.receipt_id,
            head_revision=snapshot.head_revision,
            head_digest=_head_digest(snapshot.head_revision, staged),
        )
        return EffectExecutable(proof.to_dict())


@final
class WorkflowTransitionQueueReservationExecutor:
    """Commit exactly one reservation through the aggregate authority."""

    __slots__ = ("_authority", "_clock")

    def __init__(
        self,
        *,
        authority: WorkflowTransitionQueueReservationAuthority,
        clock: Callable[[], float],
    ) -> None:
        if not isinstance(authority, WorkflowTransitionQueueReservationAuthority) or not callable(clock):
            raise WorkflowTransitionQueueReservationEffectError(
                "workflow_transition_queue_reservation_effect_executor_invalid"
            )
        self._authority = authority
        self._clock = clock

    def execute(
        self,
        attempt: WorkflowTransitionEffectAttempt,
        *,
        executable: EffectExecutable,
        heartbeat: WorkflowTransitionHeartbeatContext,
    ) -> EffectApplied | EffectRetry | EffectQuarantine:
        del heartbeat, executable
        try:
            transition, effect, generation, staged = _staged_from_attempt(attempt)
        except Exception:
            return EffectQuarantine("queue_reservation_execution_invalid")
        intent = WorkflowTransitionQueueReservationIntent(
            transition_id=staged.transition_id,
            effect_id=staged.effect_id,
            runtime_id=staged.runtime_id,
            tenant_id=staged.tenant_id,
            workflow_id=staged.workflow_id,
            run_id=staged.run_id,
            step_id=staged.step_id,
            task_id=staged.task_id,
            effect_ordinal=staged.effect_ordinal,
            queue_intent_digest=staged.queue_intent_digest,
            operation_fence_id=staged.operation_fence_id,
            attempt_id=staged.attempt_id,
            receipt_id=staged.receipt_id,
            maximum_retries=staged.maximum_retries,
            planned_at=float(effect.created_at),
        )
        try:
            receipt = self._authority.reserve_transition_queue_slot(
                intent,
                claim_generation=generation,
                reserved_at=float(self._clock()),
            )
        except WorkflowTransitionQueueReservationUnavailable:
            return EffectRetry("queue_reservation_unavailable")
        except WorkflowTransitionQueueReservationConflict:
            return EffectQuarantine("queue_reservation_conflict")
        except Exception:
            return EffectQuarantine("queue_reservation_commit_failed")
        return _applied(
            transition=transition,
            effect=effect,
            claim_generation=generation,
            receipt=receipt,
        )


def workflow_transition_queue_reservation_receipt_from_result(
    raw: Mapping[str, Any],
) -> WorkflowTransitionQueueReservationReceipt:
    """Rebuild the receipt an applied effect recorded."""

    if not isinstance(raw, Mapping) or raw.get("schema") != WORKFLOW_TRANSITION_QUEUE_RESERVATION_RESULT_SCHEMA:
        raise WorkflowTransitionQueueReservationEffectError(
            "workflow_transition_queue_reservation_effect_result_invalid"
        )
    receipt = raw.get("receipt")
    if not isinstance(receipt, Mapping):
        raise WorkflowTransitionQueueReservationEffectError(
            "workflow_transition_queue_reservation_effect_result_invalid"
        )
    return WorkflowTransitionQueueReservationReceipt(**dict(receipt))


def _result(receipt: WorkflowTransitionQueueReservationReceipt) -> dict[str, Any]:
    return {
        "schema": WORKFLOW_TRANSITION_QUEUE_RESERVATION_RESULT_SCHEMA,
        "receipt": receipt.to_dict(),
    }


def _resource_proof(
    *,
    transition: WorkflowTransition,
    effect: WorkflowTransitionEffect,
    claim_generation: int,
    receipt: WorkflowTransitionQueueReservationReceipt,
) -> WorkflowTransitionEffectResourceProof:
    return WorkflowTransitionEffectResourceProof(
        context=WorkflowTransitionEffectProofContext.from_active_claim(
            transition=transition,
            effect=effect,
            claim_generation=claim_generation,
        ),
        resource_kind=WORKFLOW_TRANSITION_QUEUE_RESERVATION_RESOURCE_KIND,
        resource_id=receipt.receipt_id,
        resource_revision=receipt.reserved_revision,
        resource_digest=receipt.receipt_digest,
    )


def _applied(
    *,
    transition: WorkflowTransition,
    effect: WorkflowTransitionEffect,
    claim_generation: int,
    receipt: WorkflowTransitionQueueReservationReceipt,
) -> EffectApplied:
    proof = _resource_proof(
        transition=transition,
        effect=effect,
        claim_generation=claim_generation,
        receipt=receipt,
    )
    return EffectApplied(_result(receipt), proof.to_dict())


def _already_applied(
    *,
    transition: WorkflowTransition,
    effect: WorkflowTransitionEffect,
    claim_generation: int,
    receipt: WorkflowTransitionQueueReservationReceipt,
) -> EffectAlreadyApplied:
    proof = _resource_proof(
        transition=transition,
        effect=effect,
        claim_generation=claim_generation,
        receipt=receipt,
    )
    return EffectAlreadyApplied(_result(receipt), proof.to_dict())


def _head_digest(head_revision: int, staged: _StagedQueueReservation) -> str:
    """Digest the observed absence so a later read cannot silently differ."""

    return workflow_transition_effect_resource_digest(
        {
            "head_revision": head_revision,
            "kind": WORKFLOW_TRANSITION_QUEUE_RESERVATION_SLOT_KIND,
            "operation_fence_id": staged.operation_fence_id,
            "receipt_id": staged.receipt_id,
            "task_id": staged.task_id,
        }
    )


def _staged(effect: WorkflowTransitionEffect) -> _StagedQueueReservation:
    payload = thaw_json(effect.payload)
    if not isinstance(payload, Mapping) or set(payload) != _EFFECT_PAYLOAD_FIELDS:
        raise WorkflowTransitionQueueReservationEffectError(
            "workflow_transition_queue_reservation_effect_payload_invalid"
        )
    if payload.get("schema") != WORKFLOW_TRANSITION_QUEUE_RESERVATION_EFFECT_SCHEMA:
        raise WorkflowTransitionQueueReservationEffectError(
            "workflow_transition_queue_reservation_effect_schema_invalid"
        )
    return _StagedQueueReservation(
        transition_id=_identity(payload["transition_id"], "transition_id"),
        effect_id=_identity(payload["effect_id"], "effect_id"),
        runtime_id=_identity(payload["runtime_id"], "runtime_id"),
        tenant_id=_identity(payload["tenant_id"], "tenant_id"),
        workflow_id=_identity(payload["workflow_id"], "workflow_id"),
        run_id=_identity(payload["run_id"], "run_id"),
        step_id=_identity(payload["step_id"], "step_id"),
        task_id=_identity(payload["task_id"], "task_id"),
        effect_ordinal=_positive_integer(payload["effect_ordinal"], "effect_ordinal"),
        queue_intent_digest=_sha256(payload["queue_intent_digest"], "queue_intent_digest"),
        operation_fence_id=_identity(payload["operation_fence_id"], "operation_fence_id"),
        attempt_id=_identity(payload["attempt_id"], "attempt_id"),
        receipt_id=_identity(payload["receipt_id"], "receipt_id"),
        maximum_retries=_maximum_retries(payload["maximum_retries"]),
    )


def _staged_from_observation(
    observation: WorkflowTransitionEffectObservation,
) -> tuple[WorkflowTransition, WorkflowTransitionEffect, int, _StagedQueueReservation]:
    if type(observation) is not WorkflowTransitionEffectObservation:
        raise WorkflowTransitionQueueReservationEffectError(
            "workflow_transition_queue_reservation_effect_observation_invalid"
        )
    return (
        observation.transition,
        observation.effect,
        observation.claim_generation,
        _staged(observation.effect),
    )


def _staged_from_attempt(
    attempt: WorkflowTransitionEffectAttempt,
) -> tuple[WorkflowTransition, WorkflowTransitionEffect, int, _StagedQueueReservation]:
    if type(attempt) is not WorkflowTransitionEffectAttempt:
        raise WorkflowTransitionQueueReservationEffectError(
            "workflow_transition_queue_reservation_effect_attempt_invalid"
        )
    return (
        attempt.transition,
        attempt.effect,
        attempt.claim_generation,
        _staged(attempt.effect),
    )


def _identity(value: object, reason: str) -> str:
    return _SCALARS.identity(value, reason)


def _sha256(value: object, reason: str) -> str:
    return _SCALARS.sha256(value, reason)


def _positive_integer(value: object, reason: str) -> int:
    return _SCALARS.positive_integer(value, reason, maximum=_MAX_DOMAIN_INTEGER)


def _maximum_retries(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2_147_483_647:
        raise WorkflowTransitionQueueReservationEffectError(
            "workflow_transition_queue_reservation_effect_maximum_retries_invalid"
        )
    return value


def _positive_float(value: object, reason: str) -> float:
    if type(value) is not float or not math.isfinite(value) or value <= 0:
        raise WorkflowTransitionQueueReservationEffectError(
            f"workflow_transition_queue_reservation_effect_{reason}_invalid"
        )
    return value


__all__ = [
    "WORKFLOW_TRANSITION_QUEUE_RESERVATION_EFFECT_SCHEMA",
    "WORKFLOW_TRANSITION_QUEUE_RESERVATION_RESOURCE_KIND",
    "WORKFLOW_TRANSITION_QUEUE_RESERVATION_RESULT_SCHEMA",
    "WORKFLOW_TRANSITION_QUEUE_RESERVATION_SLOT_KIND",
    "WorkflowTransitionQueueReservationEffectError",
    "WorkflowTransitionQueueReservationExecutor",
    "WorkflowTransitionQueueReservationObserver",
    "WorkflowTransitionQueueReservationObserverReads",
    "build_workflow_transition_queue_reservation_effect",
    "workflow_transition_queue_reservation_receipt_from_result",
]
