"""Unwired Hub-owned execution-reservation transition effect.

The adapter reserves one execution owner and records an immutable receipt in a
single authority transaction.  The receipt is durable historical evidence;
the current lease is deliberately a separate, point-in-time validity check and
must never be treated as a downstream execution capability.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, final, runtime_checkable

from agent.services.workflow_runtime.ownership import (
    WorkflowTransitionOwnershipReservationCommitPort,
    WorkflowTransitionOwnershipReservationConflict,
    WorkflowTransitionOwnershipReservationEvidence,
    WorkflowTransitionOwnershipReservationHeld,
    WorkflowTransitionOwnershipReservationHistoricalReadPort,
    WorkflowTransitionOwnershipReservationIntent,
    WorkflowTransitionOwnershipReservationReadPort,
    WorkflowTransitionOwnershipReservationReceipt,
    WorkflowTransitionOwnershipReservationStale,
    WorkflowTransitionOwnershipReservationUnavailable,
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
from agent.services.workflow_transition_outbox import (
    WorkflowTransition,
    WorkflowTransitionEffect,
)
from agent.services.workflow_transition_ownership_reservation_evidence import (
    WorkflowTransitionOwnershipReservationObserverReads,
    _absence_proof,
    _assert_absence_proof,
    _assert_historical_evidence,
    _observation_state,
    _resource_proof,
    _result,
    assert_current_workflow_transition_ownership_reservation_validity,
    assert_durable_workflow_transition_ownership_reservation_proof,
    workflow_transition_ownership_reservation_receipt_from_result,
)
from agent.services.workflow_transition_ownership_reservation_staging import (
    WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_EFFECT_SCHEMA,
    WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_RESOURCE_KIND,
    WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_RESULT_SCHEMA,
    WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_SLOT_KIND,
    WorkflowTransitionOwnershipReservationError,
    _clock_value,
    _intent_from_effect,
    _reservation_clock_value,
    build_workflow_transition_ownership_reservation_effect,
)


@runtime_checkable
class WorkflowTransitionOwnershipReservationAuthority(
    WorkflowTransitionOwnershipReservationReadPort,
    WorkflowTransitionOwnershipReservationHistoricalReadPort,
    WorkflowTransitionOwnershipReservationCommitPort,
    Protocol,
):
    """The narrow aggregate authority required by the mutating executor."""


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
