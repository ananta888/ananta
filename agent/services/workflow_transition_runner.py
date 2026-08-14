"""Hub-owned, runtime-neutral runner for durable workflow transitions.

The runner coordinates only transition leases and typed effect adapters.  It
does not know how Native, LangGraph, queue, ownership, capacity, or grant
effects work, and this module is deliberately absent from production
composition until those adapters prove idempotent adoption.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import final

from agent.services.workflow_transition_effect_execution import (
    EffectAlreadyApplied,
    EffectApplied,
    EffectExecutable,
    EffectQuarantine,
    EffectRetry,
    FinalizationObserved,
    FinalizationQuarantine,
    FinalizationRetry,
    RetryAt,
    RetryExhausted,
    WorkflowTransitionEffectAttempt,
    WorkflowTransitionEffectExecutionError,
    WorkflowTransitionEffectExecutorRegistry,
    WorkflowTransitionEffectObservation,
    WorkflowTransitionFinalizationAttempt,
    WorkflowTransitionFinalizationObserverRegistry,
    WorkflowTransitionHeartbeatContext,
    WorkflowTransitionRetryPolicy,
    workflow_transition_effect_result_envelope,
    workflow_transition_effect_stage_attempt_count,
)
from agent.services.workflow_transition_outbox import (
    EFFECT_BINDING_FINALIZE,
    EFFECT_STATE_APPLIED,
    EFFECT_STATE_APPLYING,
    EFFECT_STATE_PLANNED,
    TRANSITION_STATE_COMPLETED,
    TRANSITION_STATE_QUARANTINED,
    TRANSITION_STATE_READY,
    WorkflowTransitionCompletionPort,
    WorkflowTransitionEffect,
    WorkflowTransitionEffectPort,
    WorkflowTransitionError,
    WorkflowTransitionLeasePort,
    WorkflowTransitionQuarantinePort,
    WorkflowTransitionReadPort,
    WorkflowTransitionSnapshot,
    thaw_json,
    workflow_transition_effect_result_digest,
)

RUN_OUTCOME_COMPLETED = "completed"
RUN_OUTCOME_FENCED = "fenced"
RUN_OUTCOME_NOT_CLAIMED = "not_claimed"
RUN_OUTCOME_PROGRESSED = "progressed"
RUN_OUTCOME_QUARANTINED = "quarantined"
RUN_OUTCOME_RETRY_SCHEDULED = "retry_scheduled"
RUN_OUTCOMES = frozenset(
    {
        RUN_OUTCOME_COMPLETED,
        RUN_OUTCOME_FENCED,
        RUN_OUTCOME_NOT_CLAIMED,
        RUN_OUTCOME_PROGRESSED,
        RUN_OUTCOME_QUARANTINED,
        RUN_OUTCOME_RETRY_SCHEDULED,
    }
)

_MAX_LEASE_SECONDS = 300.0
_MAX_RETRY_HORIZON_SECONDS = 31_536_000.0


class WorkflowTransitionRunnerError(RuntimeError):
    """Stable runner configuration or state error."""


@final
@dataclass(frozen=True, slots=True)
class WorkflowTransitionRunResult:
    """Closed result of one claimed or attempted transition run."""

    outcome: str
    snapshot: WorkflowTransitionSnapshot

    def __post_init__(self) -> None:
        if self.outcome not in RUN_OUTCOMES or not isinstance(self.snapshot, WorkflowTransitionSnapshot):
            raise WorkflowTransitionRunnerError("workflow_transition_run_result_invalid")
        state = self.snapshot.transition.state
        if self.outcome == RUN_OUTCOME_COMPLETED and state != TRANSITION_STATE_COMPLETED:
            raise WorkflowTransitionRunnerError("workflow_transition_run_result_invalid")
        if self.outcome == RUN_OUTCOME_QUARANTINED and state != TRANSITION_STATE_QUARANTINED:
            raise WorkflowTransitionRunnerError("workflow_transition_run_result_invalid")
        if (
            self.outcome
            in {
                RUN_OUTCOME_PROGRESSED,
                RUN_OUTCOME_RETRY_SCHEDULED,
            }
            and state != TRANSITION_STATE_READY
        ):
            raise WorkflowTransitionRunnerError("workflow_transition_run_result_invalid")


class _GenerationHeartbeat(WorkflowTransitionHeartbeatContext):
    """Generation-bound cooperative heartbeat shared with one adapter call."""

    def __init__(
        self,
        leases: WorkflowTransitionLeasePort,
        snapshot: WorkflowTransitionSnapshot,
        *,
        owner_id: str,
        lease_seconds: float,
    ) -> None:
        self._leases = leases
        self._transition_id = snapshot.transition.transition_id
        self._owner_id = owner_id
        self._claim_generation = snapshot.transition.claim_generation
        self._lease_seconds = lease_seconds
        self.snapshot = snapshot

    @property
    def claim_generation(self) -> int:
        return self._claim_generation

    def heartbeat(self) -> None:
        self.snapshot = self._leases.heartbeat(
            self._transition_id,
            owner_id=self._owner_id,
            claim_generation=self._claim_generation,
            lease_seconds=self._lease_seconds,
        )


class WorkflowTransitionRunner:
    """Coordinate durable transition effects through injected Hub-side ports."""

    def __init__(
        self,
        *,
        reads: WorkflowTransitionReadPort,
        leases: WorkflowTransitionLeasePort,
        effects: WorkflowTransitionEffectPort,
        completion: WorkflowTransitionCompletionPort,
        quarantine: WorkflowTransitionQuarantinePort,
        effect_registry: WorkflowTransitionEffectExecutorRegistry,
        finalization_registry: WorkflowTransitionFinalizationObserverRegistry,
        retry_policy: WorkflowTransitionRetryPolicy,
        owner_id: str,
        lease_seconds: float,
        clock: Callable[[], float],
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self._reads = reads
        self._leases = leases
        self._effects = effects
        self._completion = completion
        self._quarantine_port = quarantine
        self._effect_registry = effect_registry
        self._finalization_registry = finalization_registry
        self._retry_policy = retry_policy
        self._owner_id = _owner_id(owner_id)
        self._lease_seconds = _lease_seconds(lease_seconds)
        if not callable(clock):
            raise WorkflowTransitionRunnerError("workflow_transition_runner_clock_invalid")
        self._clock = clock
        self._fault_injector = fault_injector or (lambda _stage: None)

    def run(self, transition_id: str) -> WorkflowTransitionRunResult:
        """Claim and process one transition without any implicit background loop."""

        claimed = self._leases.claim(
            transition_id,
            owner_id=self._owner_id,
            lease_seconds=self._lease_seconds,
        )
        if claimed is None:
            current = self._reads.get(transition_id)
            if current is None:
                raise WorkflowTransitionRunnerError("workflow_transition_runner_not_found")
            return WorkflowTransitionRunResult(RUN_OUTCOME_NOT_CLAIMED, current)
        return self._run_claimed(claimed)

    def drain(self, *, limit: int) -> tuple[WorkflowTransitionRunResult, ...]:
        """Claim a bounded due batch and process it synchronously in due order."""

        bounded = _limit(limit)
        results: list[WorkflowTransitionRunResult] = []
        for _ in range(bounded):
            claimed = self._leases.claim_due(
                owner_id=self._owner_id,
                lease_seconds=self._lease_seconds,
                limit=1,
            )
            if not claimed:
                break
            results.append(self._run_claimed(claimed[0]))
        return tuple(results)

    def _run_claimed(
        self,
        claimed: WorkflowTransitionSnapshot,
    ) -> WorkflowTransitionRunResult:
        heartbeat = _GenerationHeartbeat(
            self._leases,
            claimed,
            owner_id=self._owner_id,
            lease_seconds=self._lease_seconds,
        )
        while True:
            try:
                heartbeat.heartbeat()
            except WorkflowTransitionError:
                return self._fenced(claimed.transition.transition_id)
            snapshot = heartbeat.snapshot
            current = _first_nonfinal_effect(snapshot)
            if current is None:
                return self._finalize(snapshot, heartbeat)
            result = self._apply_effect(snapshot, current, heartbeat)
            if result is not None:
                return result

    def _apply_effect(
        self,
        snapshot: WorkflowTransitionSnapshot,
        effect: WorkflowTransitionEffect,
        heartbeat: _GenerationHeartbeat,
    ) -> WorkflowTransitionRunResult:
        if effect.state not in {EFFECT_STATE_PLANNED, EFFECT_STATE_APPLYING}:
            return self._quarantine(heartbeat, "effect_state_invalid")
        try:
            stage_attempt_count = _stage_attempt_count(snapshot)
        except WorkflowTransitionEffectExecutionError:
            return self._quarantine(heartbeat, "effect_result_invalid")
        try:
            handler = self._effect_registry.resolve(
                runtime_id=snapshot.transition.runtime_id,
                effect_kind=effect.kind,
            )
            observation = WorkflowTransitionEffectObservation(
                transition=snapshot.transition,
                effect=effect,
                claim_generation=heartbeat.claim_generation,
            )
            observed = handler.observation.observe_or_adopt(
                observation,
                heartbeat=heartbeat,
            )
            heartbeat.heartbeat()
        except Exception:
            return self._quarantine(heartbeat, "effect_observation_failed")
        self._fault_injector("after_effect_observation")

        if type(observed) is EffectRetry:
            return self._retry_or_quarantine(heartbeat, observed.reason_code)
        if type(observed) is EffectQuarantine:
            return self._quarantine(heartbeat, observed.reason_code)
        if type(observed) is EffectAlreadyApplied:
            try:
                begun = self._effects.begin_effect(
                    snapshot.transition.transition_id,
                    effect.effect_id,
                    owner_id=self._owner_id,
                    claim_generation=heartbeat.claim_generation,
                )
                self._fault_injector("after_effect_begin")
                adopted_result = workflow_transition_effect_result_envelope(
                    mode="adopt",
                    result_payload=observed.result_payload,
                    proof_payload=observed.proof_payload,
                    stage_attempt_count=stage_attempt_count,
                )
                self._effects.finish_effect(
                    snapshot.transition.transition_id,
                    begun.effect_id,
                    owner_id=self._owner_id,
                    claim_generation=heartbeat.claim_generation,
                    result_payload=adopted_result,
                    result_digest=workflow_transition_effect_result_digest(adopted_result),
                )
                self._fault_injector("after_effect_finish")
                heartbeat.heartbeat()
            except (WorkflowTransitionError, WorkflowTransitionEffectExecutionError):
                return self._quarantine(heartbeat, "effect_adoption_proof_invalid")
            return self._yield_progress(heartbeat, begun.effect_id)
        if type(observed) is not EffectExecutable:
            return self._quarantine(heartbeat, "effect_observation_invalid")

        try:
            authorized = self._retry_policy.authorize_attempt(attempt_count=stage_attempt_count)
        except Exception:
            return self._quarantine(heartbeat, "retry_policy_invalid")
        if type(authorized) is not bool:
            return self._quarantine(heartbeat, "retry_policy_invalid")
        if not authorized:
            return self._quarantine(heartbeat, "retry_attempts_exhausted")

        try:
            begun = self._effects.begin_effect(
                snapshot.transition.transition_id,
                effect.effect_id,
                owner_id=self._owner_id,
                claim_generation=heartbeat.claim_generation,
            )
            self._fault_injector("after_effect_begin")
            heartbeat.heartbeat()
            attempt_snapshot = heartbeat.snapshot
            applying = _effect_by_id(attempt_snapshot, begun.effect_id)
            attempt = WorkflowTransitionEffectAttempt(
                transition=attempt_snapshot.transition,
                effect=applying,
                claim_generation=heartbeat.claim_generation,
            )
            executed = handler.execution.execute(
                attempt,
                executable=observed,
                heartbeat=heartbeat,
            )
            self._fault_injector("after_effect_execution")
        except Exception:
            return self._quarantine(heartbeat, "effect_execution_uncertain")

        if type(executed) is EffectRetry:
            return self._retry_or_quarantine(heartbeat, executed.reason_code)
        if type(executed) is EffectQuarantine:
            return self._quarantine(heartbeat, executed.reason_code)
        if type(executed) is not EffectApplied:
            return self._quarantine(heartbeat, "effect_execution_invalid")
        try:
            result_payload = workflow_transition_effect_result_envelope(
                mode="execute",
                result_payload=executed.result_payload,
                proof_payload=executed.proof_payload,
                stage_attempt_count=stage_attempt_count,
            )
            self._effects.finish_effect(
                snapshot.transition.transition_id,
                effect.effect_id,
                owner_id=self._owner_id,
                claim_generation=heartbeat.claim_generation,
                result_payload=result_payload,
                result_digest=workflow_transition_effect_result_digest(result_payload),
            )
            self._fault_injector("after_effect_finish")
            heartbeat.heartbeat()
        except (WorkflowTransitionError, WorkflowTransitionEffectExecutionError):
            return self._quarantine(heartbeat, "effect_result_invalid")
        return self._yield_progress(heartbeat, effect.effect_id)

    def _finalize(
        self,
        snapshot: WorkflowTransitionSnapshot,
        heartbeat: _GenerationHeartbeat,
    ) -> WorkflowTransitionRunResult:
        try:
            _stage_attempt_count(snapshot)
        except WorkflowTransitionEffectExecutionError:
            return self._quarantine(heartbeat, "effect_result_invalid")
        try:
            observer = self._finalization_registry.resolve(
                runtime_id=snapshot.transition.runtime_id,
                transition_kind=snapshot.transition.kind,
            )
            observed = observer.observe(
                WorkflowTransitionFinalizationAttempt(
                    snapshot=snapshot,
                    claim_generation=heartbeat.claim_generation,
                ),
                heartbeat=heartbeat,
            )
            heartbeat.heartbeat()
        except Exception:
            return self._quarantine(heartbeat, "finalization_observation_failed")
        self._fault_injector("after_finalization_observation")

        if type(observed) is FinalizationRetry:
            return self._retry_or_quarantine(heartbeat, observed.reason_code)
        if type(observed) is FinalizationQuarantine:
            return self._quarantine(heartbeat, observed.reason_code)
        if type(observed) is not FinalizationObserved:
            return self._quarantine(heartbeat, "finalization_observation_invalid")
        try:
            completed = self._completion.finalize(
                snapshot.transition.transition_id,
                owner_id=self._owner_id,
                claim_generation=heartbeat.claim_generation,
                binding_status=observed.binding_status,
                checkpoint_ref=observed.checkpoint_ref,
                finalization_proof=observed.proof_payload,
            )
        except WorkflowTransitionError:
            return self._quarantine(heartbeat, "finalization_proof_invalid")
        return WorkflowTransitionRunResult(RUN_OUTCOME_COMPLETED, completed)

    def _retry_or_quarantine(
        self,
        heartbeat: _GenerationHeartbeat,
        reason_code: str,
    ) -> WorkflowTransitionRunResult:
        try:
            decision_at = _decision_at(self._clock())
        except Exception:
            return self._quarantine(heartbeat, "retry_policy_invalid")
        transition = heartbeat.snapshot.transition
        try:
            stage_attempt = _stage_attempt_count(heartbeat.snapshot)
        except WorkflowTransitionEffectExecutionError:
            return self._quarantine(heartbeat, "effect_result_invalid")
        try:
            retry = self._retry_policy.next_retry(
                attempt_count=stage_attempt,
                decision_at=decision_at,
            )
        except Exception:
            return self._quarantine(heartbeat, "retry_policy_invalid")
        if type(retry) is RetryExhausted:
            return self._quarantine(heartbeat, "retry_attempts_exhausted")
        if type(retry) is not RetryAt or not (decision_at < retry.retry_at <= decision_at + _MAX_RETRY_HORIZON_SECONDS):
            return self._quarantine(heartbeat, "retry_policy_invalid")
        try:
            released = self._leases.release(
                transition.transition_id,
                owner_id=self._owner_id,
                claim_generation=heartbeat.claim_generation,
                reason_code=reason_code,
                retry_at=retry.retry_at,
            )
        except WorkflowTransitionError:
            return self._quarantine(heartbeat, "binding_state_drift")
        return WorkflowTransitionRunResult(RUN_OUTCOME_RETRY_SCHEDULED, released)

    def _yield_progress(
        self,
        heartbeat: _GenerationHeartbeat,
        effect_id: str,
    ) -> WorkflowTransitionRunResult:
        try:
            available_at = _decision_at(self._clock())
            yielded = self._leases.yield_ready(
                heartbeat.snapshot.transition.transition_id,
                effect_id,
                owner_id=self._owner_id,
                claim_generation=heartbeat.claim_generation,
                available_at=available_at,
            )
        except WorkflowTransitionError:
            return self._quarantine(heartbeat, "binding_state_drift")
        except Exception:
            return self._quarantine(heartbeat, "progress_schedule_invalid")
        return WorkflowTransitionRunResult(RUN_OUTCOME_PROGRESSED, yielded)

    def _quarantine(
        self,
        heartbeat: _GenerationHeartbeat,
        reason_code: str,
    ) -> WorkflowTransitionRunResult:
        try:
            quarantined = self._quarantine_port.quarantine(
                heartbeat.snapshot.transition.transition_id,
                owner_id=self._owner_id,
                claim_generation=heartbeat.claim_generation,
                reason_code=reason_code,
            )
        except (WorkflowTransitionError, ValueError):
            return self._fenced(heartbeat.snapshot.transition.transition_id)
        return WorkflowTransitionRunResult(RUN_OUTCOME_QUARANTINED, quarantined)

    def _fenced(self, transition_id: str) -> WorkflowTransitionRunResult:
        current = self._reads.get(transition_id)
        if current is None:
            raise WorkflowTransitionRunnerError("workflow_transition_runner_not_found")
        return WorkflowTransitionRunResult(RUN_OUTCOME_FENCED, current)


def _first_nonfinal_effect(
    snapshot: WorkflowTransitionSnapshot,
) -> WorkflowTransitionEffect | None:
    for effect in snapshot.effects:
        if effect.kind == EFFECT_BINDING_FINALIZE:
            return None
        if effect.state != EFFECT_STATE_APPLIED:
            return effect
    return None


def _effect_by_id(
    snapshot: WorkflowTransitionSnapshot,
    effect_id: str,
) -> WorkflowTransitionEffect:
    for effect in snapshot.effects:
        if effect.effect_id == effect_id:
            return effect
    raise WorkflowTransitionRunnerError("workflow_transition_runner_effect_missing")


def _stage_attempt_count(snapshot: WorkflowTransitionSnapshot) -> int:
    transition = snapshot.transition
    if transition.attempt_count != transition.claim_generation:
        raise WorkflowTransitionEffectExecutionError("workflow_transition_header_attempt_conflict")
    previous_generation = 0
    saw_non_applied = False
    for effect in snapshot.effects:
        if effect.kind == EFFECT_BINDING_FINALIZE:
            if effect.state != EFFECT_STATE_PLANNED or effect.applied_generation != 0:
                raise WorkflowTransitionEffectExecutionError("workflow_transition_binding_finalize_effect_invalid")
            break
        if effect.state != EFFECT_STATE_APPLIED:
            saw_non_applied = True
            continue
        if saw_non_applied:
            raise WorkflowTransitionEffectExecutionError("workflow_transition_effect_order_invalid")
        if effect.applied_generation <= previous_generation or effect.applied_generation >= transition.claim_generation:
            raise WorkflowTransitionEffectExecutionError("workflow_transition_effect_application_generation_invalid")
        stage_attempts = workflow_transition_effect_stage_attempt_count(thaw_json(effect.result_payload))
        if stage_attempts != effect.applied_generation - previous_generation:
            raise WorkflowTransitionEffectExecutionError("workflow_transition_effect_stage_attempt_invalid")
        previous_generation = effect.applied_generation
    current = transition.claim_generation - previous_generation
    if current < 1:
        raise WorkflowTransitionEffectExecutionError("workflow_transition_effect_stage_attempt_invalid")
    return current


def _owner_id(value: object) -> str:
    if not isinstance(value, str):
        raise WorkflowTransitionRunnerError("workflow_transition_runner_owner_invalid")
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > 256
        or "\x00" in normalized
        or any(not character.isprintable() for character in normalized)
    ):
        raise WorkflowTransitionRunnerError("workflow_transition_runner_owner_invalid")
    return normalized


def _lease_seconds(value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not 1.0 <= float(value) <= _MAX_LEASE_SECONDS
    ):
        raise WorkflowTransitionRunnerError("workflow_transition_runner_lease_invalid")
    return float(value)


def _decision_at(value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < 0
    ):
        raise WorkflowTransitionRunnerError("workflow_transition_runner_clock_invalid")
    return float(value)


def _limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 1_000:
        raise WorkflowTransitionRunnerError("workflow_transition_runner_limit_invalid")
    return value


__all__ = [
    "RUN_OUTCOME_COMPLETED",
    "RUN_OUTCOME_FENCED",
    "RUN_OUTCOME_NOT_CLAIMED",
    "RUN_OUTCOME_PROGRESSED",
    "RUN_OUTCOME_QUARANTINED",
    "RUN_OUTCOME_RETRY_SCHEDULED",
    "WorkflowTransitionRunResult",
    "WorkflowTransitionRunner",
    "WorkflowTransitionRunnerError",
]
