"""Unwired Hub-owned execution-reservation transition effect.

The adapter reserves one execution owner and records an immutable receipt in a
single authority transaction.  The receipt is durable historical evidence;
the current lease is deliberately a separate, point-in-time validity check and
must never be treated as a downstream execution capability.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, final, runtime_checkable

from agent.services.workflow_runtime.ownership import (
    ExecutionOwnership,
    WorkflowTransitionOwnershipReservationCommitPort,
    WorkflowTransitionOwnershipReservationConflict,
    WorkflowTransitionOwnershipReservationEvidence,
    WorkflowTransitionOwnershipReservationHeld,
    WorkflowTransitionOwnershipReservationHistoricalReadPort,
    WorkflowTransitionOwnershipReservationIntent,
    WorkflowTransitionOwnershipReservationObservation,
    WorkflowTransitionOwnershipReservationReadPort,
    WorkflowTransitionOwnershipReservationReceipt,
    WorkflowTransitionOwnershipReservationStale,
    WorkflowTransitionOwnershipReservationUnavailable,
    workflow_transition_ownership_attempt_id,
    workflow_transition_ownership_intent_digest,
    workflow_transition_ownership_operation_fence_id,
    workflow_transition_ownership_owner_id,
    workflow_transition_ownership_receipt_id,
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
    assert_active_workflow_transition_effect_absence_proof_binding,
    assert_durable_workflow_transition_effect_proof_binding,
)
from agent.services.workflow_transition_outbox import (
    EFFECT_OWNERSHIP_RESERVE,
    TRANSITION_RUNTIMES,
    WorkflowTransition,
    WorkflowTransitionEffect,
    thaw_json,
    workflow_transition_effect_id,
    workflow_transition_effect_stage_attempt_count,
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
_RESULT_FIELDS = frozenset({"schema", "receipt"})
_MAX_DOMAIN_INTEGER = 2**63 - 1
_MAX_OWNERSHIP_COUNTER = 2_147_483_647
_MAXIMUM_RETRIES = 2_147_483_647


class WorkflowTransitionOwnershipReservationError(ValueError):
    """Stable fail-closed effect, result, or proof binding error."""


_SCALARS = WorkflowTransitionEffectScalars(
    error=WorkflowTransitionOwnershipReservationError,
    prefix="workflow_transition_ownership_reservation",
)


@runtime_checkable
class WorkflowTransitionOwnershipReservationAuthority(
    WorkflowTransitionOwnershipReservationReadPort,
    WorkflowTransitionOwnershipReservationHistoricalReadPort,
    WorkflowTransitionOwnershipReservationCommitPort,
    Protocol,
):
    """The narrow aggregate authority required by the mutating executor."""


@runtime_checkable
class WorkflowTransitionOwnershipReservationObserverReads(
    WorkflowTransitionOwnershipReservationReadPort,
    WorkflowTransitionOwnershipReservationHistoricalReadPort,
    Protocol,
):
    """Current observation plus current-independent historical evidence."""


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


@final
class WorkflowTransitionOwnershipReservationObserver:
    """Observe exact reservation state without mutating ownership or retry budget."""

    __slots__ = ("_clock", "_reads")

    def __init__(
        self,
        *,
        reads: WorkflowTransitionOwnershipReservationObserverReads,
        clock: Callable[[], float],
    ) -> None:
        if not isinstance(reads, WorkflowTransitionOwnershipReservationObserverReads) or not callable(clock):
            raise WorkflowTransitionOwnershipReservationError(
                "workflow_transition_ownership_reservation_observer_invalid"
            )
        self._reads = reads
        self._clock = clock

    def observe_or_adopt(
        self,
        observation: WorkflowTransitionEffectObservation,
        *,
        heartbeat: WorkflowTransitionHeartbeatContext,
    ) -> EffectAlreadyApplied | EffectExecutable | EffectRetry | EffectQuarantine:
        del heartbeat
        try:
            transition, effect, generation, intent = _intent_from_observation(observation)
        except Exception:
            return EffectQuarantine("ownership_reservation_observation_invalid")
        historical = _read_historical_result(
            self._reads,
            intent=intent,
            claim_generation=generation,
        )
        if isinstance(historical, (EffectRetry, EffectQuarantine)):
            return historical
        if historical is not None:
            return _already_applied(
                transition=transition,
                effect=effect,
                claim_generation=generation,
                receipt=historical,
            )
        try:
            snapshot = self._reads.observe_transition_reservation(
                intent,
                claim_generation=generation,
            )
        except WorkflowTransitionOwnershipReservationConflict:
            raced = _read_historical_result(
                self._reads,
                intent=intent,
                claim_generation=generation,
            )
            if isinstance(raced, WorkflowTransitionOwnershipReservationReceipt):
                return _already_applied(
                    transition=transition,
                    effect=effect,
                    claim_generation=generation,
                    receipt=raced,
                )
            if isinstance(raced, (EffectRetry, EffectQuarantine)):
                return raced
            return EffectQuarantine("ownership_reservation_observation_conflict")
        except (
            WorkflowTransitionOwnershipReservationHeld,
            WorkflowTransitionOwnershipReservationStale,
            WorkflowTransitionOwnershipReservationUnavailable,
        ):
            raced = _read_historical_result(
                self._reads,
                intent=intent,
                claim_generation=generation,
            )
            if isinstance(raced, WorkflowTransitionOwnershipReservationReceipt):
                return _already_applied(
                    transition=transition,
                    effect=effect,
                    claim_generation=generation,
                    receipt=raced,
                )
            if isinstance(raced, EffectQuarantine):
                return raced
            return EffectRetry("ownership_reservation_observation_retry")
        except Exception:
            raced = _read_historical_result(
                self._reads,
                intent=intent,
                claim_generation=generation,
            )
            if isinstance(raced, WorkflowTransitionOwnershipReservationReceipt):
                return _already_applied(
                    transition=transition,
                    effect=effect,
                    claim_generation=generation,
                    receipt=raced,
                )
            if isinstance(raced, EffectQuarantine):
                return raced
            return EffectRetry("ownership_reservation_observation_retry")
        try:
            now = _clock_value(self._clock)
            state = _observation_state(
                snapshot,
                intent=intent,
                claim_generation=generation,
                now=now,
            )
            if state == "receipt":
                raced = _read_historical_result(
                    self._reads,
                    intent=intent,
                    claim_generation=generation,
                )
                if isinstance(raced, (EffectRetry, EffectQuarantine)):
                    return raced
                if raced is None:
                    return EffectQuarantine("ownership_reservation_observation_conflict")
                return _already_applied(
                    transition=transition,
                    effect=effect,
                    claim_generation=generation,
                    receipt=raced,
                )
            if state == "held":
                return EffectRetry("ownership_reservation_lease_held")
            if state == "retry_exhausted":
                return EffectQuarantine("ownership_reservation_retry_exhausted")
            if state == "counter_exhausted":
                return EffectQuarantine("ownership_reservation_counter_exhausted")
            if state == "executable":
                return EffectExecutable(
                    _absence_proof(
                        transition=transition,
                        effect=effect,
                        claim_generation=generation,
                        snapshot=snapshot,
                    ).to_dict()
                )
            return EffectQuarantine("ownership_reservation_observation_conflict")
        except Exception:
            return EffectQuarantine("ownership_reservation_observation_conflict")


@final
class WorkflowTransitionOwnershipReservationExecutor:
    """Commit current/history/retry/receipt through one aggregate authority."""

    __slots__ = ("_authority", "_clock")

    def __init__(
        self,
        *,
        authority: WorkflowTransitionOwnershipReservationAuthority,
        clock: Callable[[], float],
    ) -> None:
        if not isinstance(authority, WorkflowTransitionOwnershipReservationAuthority) or not callable(clock):
            raise WorkflowTransitionOwnershipReservationError(
                "workflow_transition_ownership_reservation_executor_invalid"
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
        del heartbeat
        try:
            transition, effect, generation, intent = _intent_from_attempt(attempt)
        except Exception:
            return EffectQuarantine("ownership_reservation_execution_invalid")
        historical = _read_historical_result(
            self._authority,
            intent=intent,
            claim_generation=generation,
        )
        if isinstance(historical, (EffectRetry, EffectQuarantine)):
            return historical
        if historical is not None:
            return _applied(
                transition=transition,
                effect=effect,
                claim_generation=generation,
                receipt=historical,
            )
        try:
            before = self._authority.observe_transition_reservation(
                intent,
                claim_generation=generation,
            )
        except WorkflowTransitionOwnershipReservationConflict:
            raced = _read_historical_result(
                self._authority,
                intent=intent,
                claim_generation=generation,
            )
            if isinstance(raced, WorkflowTransitionOwnershipReservationReceipt):
                return _applied(
                    transition=transition,
                    effect=effect,
                    claim_generation=generation,
                    receipt=raced,
                )
            if isinstance(raced, (EffectRetry, EffectQuarantine)):
                return raced
            return EffectQuarantine("ownership_reservation_execution_conflict")
        except (
            WorkflowTransitionOwnershipReservationHeld,
            WorkflowTransitionOwnershipReservationStale,
            WorkflowTransitionOwnershipReservationUnavailable,
        ):
            raced = _read_historical_result(
                self._authority,
                intent=intent,
                claim_generation=generation,
            )
            if isinstance(raced, WorkflowTransitionOwnershipReservationReceipt):
                return _applied(
                    transition=transition,
                    effect=effect,
                    claim_generation=generation,
                    receipt=raced,
                )
            if isinstance(raced, EffectQuarantine):
                return raced
            return EffectRetry("ownership_reservation_execution_retry")
        except Exception:
            raced = _read_historical_result(
                self._authority,
                intent=intent,
                claim_generation=generation,
            )
            if isinstance(raced, WorkflowTransitionOwnershipReservationReceipt):
                return _applied(
                    transition=transition,
                    effect=effect,
                    claim_generation=generation,
                    receipt=raced,
                )
            if isinstance(raced, EffectQuarantine):
                return raced
            return EffectRetry("ownership_reservation_execution_retry")
        try:
            reserved_at = _reservation_clock_value(self._clock, intent=intent)
        except Exception:
            return EffectQuarantine("ownership_reservation_clock_invalid")
        try:
            state = _observation_state(
                before,
                intent=intent,
                claim_generation=generation,
                now=reserved_at,
            )
            if state == "receipt":
                raced = _read_historical_result(
                    self._authority,
                    intent=intent,
                    claim_generation=generation,
                )
                if isinstance(raced, (EffectRetry, EffectQuarantine)):
                    return raced
                if raced is None:
                    return EffectQuarantine("ownership_reservation_execution_conflict")
                return _applied(
                    transition=transition,
                    effect=effect,
                    claim_generation=generation,
                    receipt=raced,
                )
            if state == "held":
                return EffectRetry("ownership_reservation_lease_held")
            if state == "retry_exhausted":
                return EffectQuarantine("ownership_reservation_retry_exhausted")
            if state == "counter_exhausted":
                return EffectQuarantine("ownership_reservation_counter_exhausted")
            if state != "executable":
                return EffectQuarantine("ownership_reservation_execution_conflict")
            if type(executable) is not EffectExecutable:
                raise WorkflowTransitionOwnershipReservationError(
                    "workflow_transition_ownership_reservation_executable_invalid"
                )
            _assert_absence_proof(
                executable.proof_payload,
                transition=transition,
                effect=effect,
                claim_generation=generation,
                snapshot=before,
            )
        except Exception:
            return EffectQuarantine("ownership_reservation_executable_proof_invalid")

        try:
            committed = self._authority.reserve_transition_effect(
                intent,
                creator_claim_generation=generation,
                expected_observation_digest=before.observation_digest,
                reserved_at=reserved_at,
            )
        except WorkflowTransitionOwnershipReservationStale:
            return self._resolve_commit_exception(
                transition=transition,
                effect=effect,
                claim_generation=generation,
                intent=intent,
                proven_conflict=False,
            )
        except WorkflowTransitionOwnershipReservationConflict:
            return self._resolve_commit_exception(
                transition=transition,
                effect=effect,
                claim_generation=generation,
                intent=intent,
                proven_conflict=True,
            )
        except Exception:
            return self._resolve_commit_exception(
                transition=transition,
                effect=effect,
                claim_generation=generation,
                intent=intent,
                proven_conflict=False,
            )
        try:
            evidence = self._authority.read_transition_reservation_history(intent)
        except WorkflowTransitionOwnershipReservationConflict:
            return EffectQuarantine("ownership_reservation_commit_conflict")
        except Exception:
            return EffectRetry("ownership_reservation_commit_read_retry")
        try:
            receipt = _assert_historical_evidence(
                evidence,
                intent=intent,
                claim_generation=generation,
            )
            if receipt != committed:
                return EffectQuarantine("ownership_reservation_commit_missing")
            return _applied(
                transition=transition,
                effect=effect,
                claim_generation=generation,
                receipt=receipt,
            )
        except Exception:
            return EffectQuarantine("ownership_reservation_commit_conflict")

    def _resolve_commit_exception(
        self,
        *,
        transition: WorkflowTransition,
        effect: WorkflowTransitionEffect,
        claim_generation: int,
        intent: WorkflowTransitionOwnershipReservationIntent,
        proven_conflict: bool,
    ) -> EffectApplied | EffectRetry | EffectQuarantine:
        try:
            evidence = self._authority.read_transition_reservation_history(intent)
        except WorkflowTransitionOwnershipReservationConflict:
            return EffectQuarantine("ownership_reservation_commit_conflict")
        except Exception:
            return EffectRetry("ownership_reservation_commit_read_retry")
        try:
            if evidence.receipt is not None:
                receipt = _assert_historical_evidence(
                    evidence,
                    intent=intent,
                    claim_generation=claim_generation,
                )
                return _applied(
                    transition=transition,
                    effect=effect,
                    claim_generation=claim_generation,
                    receipt=receipt,
                )
        except Exception:
            return EffectQuarantine("ownership_reservation_commit_conflict")
        if proven_conflict:
            return EffectQuarantine("ownership_reservation_commit_conflict")
        try:
            after = self._authority.observe_transition_reservation(
                intent,
                claim_generation=claim_generation,
            )
        except WorkflowTransitionOwnershipReservationConflict:
            return EffectQuarantine("ownership_reservation_commit_conflict")
        except Exception:
            return EffectRetry("ownership_reservation_commit_read_retry")
        try:
            state = _observation_state(
                after,
                intent=intent,
                claim_generation=claim_generation,
                now=_clock_value(self._clock),
            )
            if state == "receipt":
                raced = _read_historical_result(
                    self._authority,
                    intent=intent,
                    claim_generation=claim_generation,
                )
                if isinstance(raced, WorkflowTransitionOwnershipReservationReceipt):
                    return _applied(
                        transition=transition,
                        effect=effect,
                        claim_generation=claim_generation,
                        receipt=raced,
                    )
                if isinstance(raced, (EffectRetry, EffectQuarantine)):
                    return raced
                return EffectQuarantine("ownership_reservation_commit_conflict")
            if state in {"executable", "held"}:
                return EffectRetry("ownership_reservation_commit_retry")
            return EffectQuarantine("ownership_reservation_commit_conflict")
        except Exception:
            return EffectQuarantine("ownership_reservation_commit_conflict")


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


def _intent_from_observation(
    observation: WorkflowTransitionEffectObservation,
) -> tuple[
    WorkflowTransition,
    WorkflowTransitionEffect,
    int,
    WorkflowTransitionOwnershipReservationIntent,
]:
    if type(observation) is not WorkflowTransitionEffectObservation:
        raise WorkflowTransitionOwnershipReservationError(
            "workflow_transition_ownership_reservation_observation_invalid"
        )
    return (
        observation.transition,
        observation.effect,
        observation.claim_generation,
        _intent_from_effect(observation.effect, transition=observation.transition),
    )


def _intent_from_attempt(
    attempt: WorkflowTransitionEffectAttempt,
) -> tuple[
    WorkflowTransition,
    WorkflowTransitionEffect,
    int,
    WorkflowTransitionOwnershipReservationIntent,
]:
    if type(attempt) is not WorkflowTransitionEffectAttempt:
        raise WorkflowTransitionOwnershipReservationError("workflow_transition_ownership_reservation_attempt_invalid")
    return (
        attempt.transition,
        attempt.effect,
        attempt.claim_generation,
        _intent_from_effect(attempt.effect, transition=attempt.transition),
    )


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


def _read_historical_result(
    reads: WorkflowTransitionOwnershipReservationHistoricalReadPort,
    *,
    intent: WorkflowTransitionOwnershipReservationIntent,
    claim_generation: int,
) -> WorkflowTransitionOwnershipReservationReceipt | EffectRetry | EffectQuarantine | None:
    try:
        evidence = reads.read_transition_reservation_history(intent)
    except WorkflowTransitionOwnershipReservationConflict:
        return EffectQuarantine("ownership_reservation_history_conflict")
    except Exception:
        return EffectRetry("ownership_reservation_history_retry")
    if type(evidence) is not WorkflowTransitionOwnershipReservationEvidence or evidence.intent != intent:
        return EffectQuarantine("ownership_reservation_history_conflict")
    if evidence.receipt is None:
        return None
    try:
        return _assert_historical_evidence(
            evidence,
            intent=intent,
            claim_generation=claim_generation,
        )
    except Exception:
        return EffectQuarantine("ownership_reservation_history_conflict")


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


def _already_applied(
    *,
    transition: WorkflowTransition,
    effect: WorkflowTransitionEffect,
    claim_generation: int,
    receipt: WorkflowTransitionOwnershipReservationReceipt,
) -> EffectAlreadyApplied:
    proof = _resource_proof(
        transition=transition,
        effect=effect,
        claim_generation=claim_generation,
        receipt=receipt,
    )
    return EffectAlreadyApplied(_result(receipt), proof.to_dict())


def _applied(
    *,
    transition: WorkflowTransition,
    effect: WorkflowTransitionEffect,
    claim_generation: int,
    receipt: WorkflowTransitionOwnershipReservationReceipt,
) -> EffectApplied:
    proof = _resource_proof(
        transition=transition,
        effect=effect,
        claim_generation=claim_generation,
        receipt=receipt,
    )
    return EffectApplied(_result(receipt), proof.to_dict())


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
    "WorkflowTransitionOwnershipReservationAuthority",
    "WorkflowTransitionOwnershipReservationError",
    "WorkflowTransitionOwnershipReservationExecutor",
    "WorkflowTransitionOwnershipReservationObserver",
    "WorkflowTransitionOwnershipReservationObserverReads",
    "assert_current_workflow_transition_ownership_reservation_validity",
    "assert_durable_workflow_transition_ownership_reservation_proof",
    "build_workflow_transition_ownership_reservation_effect",
    "workflow_transition_ownership_reservation_receipt_from_result",
]
