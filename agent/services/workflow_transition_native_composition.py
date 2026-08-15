"""Native command-transition cutover composition.

This module turns the previously unwired transition track into a runnable
Native slice.  It contributes the four pieces the track deliberately left
open: a concrete command planner, the exact effect/finalization registries,
an authoritative binding observer, and a bounded drain driver.

The planned slice is intentionally narrow.  One command transition reserves
exactly one execution owner, appends exactly one Hub-owned event, and then
finalizes the binding against authoritative runtime status.  Queue, checkpoint
and authorization-grant effects have no adapter yet and are therefore never
planned; a plan that needed them would fail closed at registry resolution
rather than silently skip them.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, final, runtime_checkable

from agent.services.workflow_command_transition_admission import WorkflowCommandTransitionIntent
from agent.services.workflow_control_bindings import WorkflowControlRunBinding
from agent.services.workflow_control_command_receipts import WorkflowControlCommandReceipt
from agent.services.workflow_runtime.events import (
    WorkflowTransitionEventObservationReadPort,
)
from agent.services.workflow_transition_effect_execution import (
    FinalizationObservationResult,
    FinalizationObserved,
    FinalizationQuarantine,
    FinalizationRetry,
    WorkflowTransitionEffectExecutorRegistry,
    WorkflowTransitionEffectHandler,
    WorkflowTransitionEffectRegistration,
    WorkflowTransitionFinalizationAttempt,
    WorkflowTransitionFinalizationObserverRegistry,
    WorkflowTransitionFinalizationRegistration,
    WorkflowTransitionHeartbeatContext,
)
from agent.services.workflow_transition_event_effect import (
    WorkflowTransitionEventAuthority,
    WorkflowTransitionEventEffectExecutor,
    WorkflowTransitionEventEffectObserver,
    build_workflow_transition_event_effect,
    workflow_transition_event_effect_idempotency_key,
    workflow_transition_event_id,
)
from agent.services.workflow_transition_outbox import (
    EFFECT_BINDING_FINALIZE,
    EFFECT_EVENT_APPEND,
    EFFECT_OWNERSHIP_RESERVE,
    TRANSITION_KIND_COMMAND,
    TRANSITION_RUNTIME_NATIVE,
    WorkflowTransition,
    WorkflowTransitionEffect,
    WorkflowTransitionReadPort,
    workflow_transition_effect_id,
)
from agent.services.workflow_transition_ownership_reservation import (
    WorkflowTransitionOwnershipReservationAuthority,
    WorkflowTransitionOwnershipReservationExecutor,
    WorkflowTransitionOwnershipReservationObserver,
    WorkflowTransitionOwnershipReservationObserverReads,
    build_workflow_transition_ownership_reservation_effect,
)

NATIVE_COMMAND_EVENT_TYPE = "workflow.command.admitted"

_OWNERSHIP_ORDINAL = 1
_EVENT_ORDINAL = 2
_FINALIZE_ORDINAL = 3
_MAX_DRAIN_LIMIT = 256


class WorkflowTransitionNativeCompositionError(RuntimeError):
    """Stable fail-closed Native cutover composition error."""


@runtime_checkable
class WorkflowBindingStatusReadPort(Protocol):
    """Read authoritative runtime status for one workflow binding."""

    def get_workflow_status(self, workflow_id: str) -> Mapping[str, Any]: ...


@final
class NativeCommandTransitionIntentFactory:
    """Plan one Native command transition as ownership, event and finalize.

    Planning is adoption-first.  A transition that is already staged is
    returned verbatim from the store instead of being replanned, because the
    event effect pins the event head sequence observed at planning time: once
    this transition's own event has landed, a replan would derive a different
    expected sequence and therefore a different effect fingerprint for the
    same command.  Recovery must adopt the exact original attempt, so the
    persisted plan — not a fresh one — is the source of truth.

    Only a genuinely new transition reads the head, through the same exact
    read port the adapter later re-reads.  A foreign append between plan and
    execution is therefore never silently absorbed: the adapter quarantines on
    sequence drift instead of appending a misattributed event.
    """

    __slots__ = ("_events", "_lease_seconds", "_maximum_retries", "_transitions")

    def __init__(
        self,
        *,
        events: WorkflowTransitionEventObservationReadPort,
        transitions: WorkflowTransitionReadPort,
        lease_seconds: float = 30.0,
        maximum_retries: int = 3,
    ) -> None:
        if not callable(getattr(events, "observe_transition_event", None)):
            raise WorkflowTransitionNativeCompositionError("workflow_transition_native_events_invalid")
        if not callable(getattr(transitions, "get", None)):
            raise WorkflowTransitionNativeCompositionError("workflow_transition_native_transitions_invalid")
        if (
            isinstance(lease_seconds, bool)
            or not isinstance(lease_seconds, (int, float))
            or not math.isfinite(float(lease_seconds))
            or float(lease_seconds) <= 0
        ):
            raise WorkflowTransitionNativeCompositionError("workflow_transition_native_lease_invalid")
        if isinstance(maximum_retries, bool) or not isinstance(maximum_retries, int) or maximum_retries < 1:
            raise WorkflowTransitionNativeCompositionError("workflow_transition_native_retries_invalid")
        self._events = events
        self._transitions = transitions
        self._lease_seconds = float(lease_seconds)
        self._maximum_retries = int(maximum_retries)

    def build(
        self,
        *,
        receipt: WorkflowControlCommandReceipt,
        binding: WorkflowControlRunBinding,
        transition_id: str,
        planned_at: float,
    ) -> WorkflowCommandTransitionIntent:
        existing = self._transitions.get(transition_id)
        if existing is not None:
            return WorkflowCommandTransitionIntent(existing.transition, tuple(existing.effects))
        admitted = receipt.request_payload.get("admitted_command")
        if not isinstance(admitted, Mapping):
            raise WorkflowTransitionNativeCompositionError("workflow_transition_native_admitted_command_invalid")
        step_id = _step_id(receipt)
        ownership = build_workflow_transition_ownership_reservation_effect(
            transition_id=transition_id,
            tenant_id=binding.tenant_id,
            workflow_id=binding.workflow_id,
            run_id=binding.run_id,
            runtime_id=TRANSITION_RUNTIME_NATIVE,
            ordinal=_OWNERSHIP_ORDINAL,
            step_id=step_id,
            lease_seconds=self._lease_seconds,
            maximum_retries=self._maximum_retries,
            planned_at=planned_at,
        )
        event = build_workflow_transition_event_effect(
            transition_id=transition_id,
            tenant_id=binding.tenant_id,
            workflow_id=binding.workflow_id,
            run_id=binding.run_id,
            ordinal=_EVENT_ORDINAL,
            event_type=NATIVE_COMMAND_EVENT_TYPE,
            step_id=step_id,
            payload={
                "command_id": receipt.command_id,
                "command": dict(admitted),
                "run_id": binding.run_id,
                "step_id": step_id,
            },
            expected_sequence=self._head_sequence(
                binding=binding,
                transition_id=transition_id,
            ),
            planned_at=planned_at,
        )
        finalize = WorkflowTransitionEffect.build(
            transition_id=transition_id,
            ordinal=_FINALIZE_ORDINAL,
            kind=EFFECT_BINDING_FINALIZE,
            idempotency_key=binding.workflow_id,
            payload={"workflow_id": binding.workflow_id},
            created_at=planned_at,
        )
        effects = (ownership, event, finalize)
        transition = WorkflowTransition.build(
            transition_id=transition_id,
            tenant_id=binding.tenant_id,
            workflow_id=binding.workflow_id,
            run_id=binding.run_id,
            runtime_id=TRANSITION_RUNTIME_NATIVE,
            kind=TRANSITION_KIND_COMMAND,
            command_id=receipt.command_id,
            receipt_id=receipt.command_id,
            admitted_command=dict(admitted),
            request_payload=receipt.request_payload,
            effects=effects,
            expected_revision=receipt.expected_revision,
            expected_checkpoint_ref=receipt.checkpoint_ref,
            created_at=planned_at,
        )
        return WorkflowCommandTransitionIntent(transition, effects)

    def _head_sequence(
        self,
        *,
        binding: WorkflowControlRunBinding,
        transition_id: str,
    ) -> int:
        dedupe_key = workflow_transition_event_effect_idempotency_key(
            transition_id=transition_id,
            ordinal=_EVENT_ORDINAL,
            event_type=NATIVE_COMMAND_EVENT_TYPE,
        )
        effect_id = workflow_transition_effect_id(
            transition_id=transition_id,
            ordinal=_EVENT_ORDINAL,
            kind=EFFECT_EVENT_APPEND,
            idempotency_key=dedupe_key,
        )
        snapshot = self._events.observe_transition_event(
            tenant_id=binding.tenant_id,
            workflow_id=binding.workflow_id,
            run_id=binding.run_id,
            dedupe_key=dedupe_key,
            event_id=workflow_transition_event_id(
                transition_id=transition_id,
                effect_id=effect_id,
            ),
        )
        head = getattr(snapshot, "head_sequence", None)
        if isinstance(head, bool) or not isinstance(head, int) or head < 0:
            raise WorkflowTransitionNativeCompositionError("workflow_transition_native_head_sequence_invalid")
        return head


@final
class NativeBindingFinalizationObserver:
    """Observe authoritative Native binding status for one command transition.

    The observer never mutates runtime state and never invents evidence.  A
    status that is not yet consistent with the claimed transition is reported
    as retryable rather than finalized, so a late runtime never completes a
    transition against a stale revision.
    """

    __slots__ = ("_status_reads",)

    def __init__(self, *, status_reads: WorkflowBindingStatusReadPort) -> None:
        if not isinstance(status_reads, WorkflowBindingStatusReadPort):
            raise WorkflowTransitionNativeCompositionError("workflow_transition_native_status_reads_invalid")
        self._status_reads = status_reads

    def observe(
        self,
        attempt: WorkflowTransitionFinalizationAttempt,
        *,
        heartbeat: WorkflowTransitionHeartbeatContext,
    ) -> FinalizationObservationResult:
        del heartbeat
        transition = attempt.snapshot.transition
        try:
            observed = self._status_reads.get_workflow_status(transition.workflow_id)
        except Exception:
            return FinalizationRetry("native_binding_status_unavailable")
        if not isinstance(observed, Mapping):
            return FinalizationQuarantine("native_binding_status_invalid")
        status = dict(observed)
        revision = status.get("revision")
        checkpoint_ref = status.get("checkpoint_ref")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            return FinalizationQuarantine("native_binding_revision_invalid")
        if not isinstance(checkpoint_ref, str) or not checkpoint_ref:
            return FinalizationQuarantine("native_binding_checkpoint_invalid")
        if revision < transition.expected_revision:
            return FinalizationRetry("native_binding_revision_behind")
        try:
            return FinalizationObserved(
                status,
                checkpoint_ref,
                {
                    "observation_revision": revision,
                    "transition_id": transition.transition_id,
                },
            )
        except Exception:
            return FinalizationQuarantine("native_binding_evidence_invalid")


@final
class NativeTransitionPublicProjector:
    """Derive the public status a finalized Native transition publishes.

    The projection is deliberately minimal and derived only from authoritative
    raw status: it republishes the observed lifecycle status, revision and
    checkpoint reference and invents nothing.  A deployment that needs the
    richer binding-aware projection can substitute any other implementation of
    the same port without touching the runner or the adapters.
    """

    __slots__ = ()

    def project(
        self,
        *,
        transition: WorkflowTransition,
        binding: Mapping[str, Any],
        binding_status: Mapping[str, Any],
        previous_public_status: Mapping[str, Any] | None,
    ) -> Mapping[str, Any]:
        del binding, previous_public_status
        revision = binding_status.get("revision")
        checkpoint_ref = binding_status.get("checkpoint_ref")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise WorkflowTransitionNativeCompositionError("workflow_transition_native_public_revision_invalid")
        if not isinstance(checkpoint_ref, str) or not checkpoint_ref:
            raise WorkflowTransitionNativeCompositionError("workflow_transition_native_public_checkpoint_invalid")
        status = binding_status.get("status")
        return {
            "checkpoint_ref": checkpoint_ref,
            "revision": revision,
            "run_id": transition.run_id,
            "runtime_id": transition.runtime_id,
            "status": status if isinstance(status, str) and status else "unknown",
            "workflow_id": transition.workflow_id,
        }


def build_native_transition_effect_registry(
    *,
    ownership_authority: WorkflowTransitionOwnershipReservationAuthority,
    ownership_reads: WorkflowTransitionOwnershipReservationObserverReads,
    event_authority: WorkflowTransitionEventAuthority,
    event_reads: WorkflowTransitionEventObservationReadPort,
    clock: Callable[[], float],
) -> WorkflowTransitionEffectExecutorRegistry:
    """Register exactly the Native effect kinds that have a proven adapter."""

    if not callable(clock):
        raise WorkflowTransitionNativeCompositionError("workflow_transition_native_clock_invalid")
    return WorkflowTransitionEffectExecutorRegistry(
        (
            WorkflowTransitionEffectRegistration(
                runtime_id=TRANSITION_RUNTIME_NATIVE,
                effect_kind=EFFECT_OWNERSHIP_RESERVE,
                handler=WorkflowTransitionEffectHandler(
                    observation=WorkflowTransitionOwnershipReservationObserver(
                        reads=ownership_reads,
                        clock=clock,
                    ),
                    execution=WorkflowTransitionOwnershipReservationExecutor(
                        authority=ownership_authority,
                        clock=clock,
                    ),
                ),
            ),
            WorkflowTransitionEffectRegistration(
                runtime_id=TRANSITION_RUNTIME_NATIVE,
                effect_kind=EFFECT_EVENT_APPEND,
                handler=WorkflowTransitionEffectHandler(
                    observation=WorkflowTransitionEventEffectObserver(
                        runtime_id=TRANSITION_RUNTIME_NATIVE,
                        reads=event_reads,
                    ),
                    execution=WorkflowTransitionEventEffectExecutor(
                        runtime_id=TRANSITION_RUNTIME_NATIVE,
                        authority=event_authority,
                    ),
                ),
            ),
        )
    )


def build_native_transition_finalization_registry(
    *,
    status_reads: WorkflowBindingStatusReadPort,
) -> WorkflowTransitionFinalizationObserverRegistry:
    """Register the authoritative Native command finalization observer."""

    return WorkflowTransitionFinalizationObserverRegistry(
        (
            WorkflowTransitionFinalizationRegistration(
                runtime_id=TRANSITION_RUNTIME_NATIVE,
                transition_kind=TRANSITION_KIND_COMMAND,
                observation=NativeBindingFinalizationObserver(status_reads=status_reads),
            ),
        )
    )


@runtime_checkable
class WorkflowTransitionDrainPort(Protocol):
    """The single runner capability the driver is allowed to use."""

    def drain(self, *, limit: int) -> tuple[Any, ...]: ...


@final
@dataclass(frozen=True, slots=True)
class WorkflowTransitionDriveReport:
    """Closed, countable outcome of one bounded drive tick."""

    processed: int
    outcomes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_id": TRANSITION_RUNTIME_NATIVE,
            "processed": self.processed,
            "outcomes": list(self.outcomes),
        }


@final
class WorkflowTransitionDriver:
    """Drive due transitions in bounded batches without a background thread.

    The driver owns no scheduling policy of its own.  It is called from the
    same reconcile path that already drains dispatch work, so a deployment
    that never calls it simply leaves transitions durably staged.
    """

    __slots__ = ("_limit", "_runner")

    def __init__(self, *, runner: WorkflowTransitionDrainPort, limit: int = 32) -> None:
        if not isinstance(runner, WorkflowTransitionDrainPort):
            raise WorkflowTransitionNativeCompositionError("workflow_transition_native_runner_invalid")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _MAX_DRAIN_LIMIT:
            raise WorkflowTransitionNativeCompositionError("workflow_transition_native_limit_invalid")
        self._runner = runner
        self._limit = int(limit)

    def tick(self, *, limit: int | None = None) -> WorkflowTransitionDriveReport:
        bounded = self._limit if limit is None else limit
        if isinstance(bounded, bool) or not isinstance(bounded, int) or not 1 <= bounded <= _MAX_DRAIN_LIMIT:
            raise WorkflowTransitionNativeCompositionError("workflow_transition_native_limit_invalid")
        results = self._runner.drain(limit=bounded)
        outcomes = tuple(str(getattr(result, "outcome", "")) for result in results)
        return WorkflowTransitionDriveReport(len(outcomes), outcomes)


def _step_id(receipt: WorkflowControlCommandReceipt) -> str:
    """Derive the transition step identity from the admitted command."""

    admitted = receipt.request_payload.get("admitted_command")
    candidate = admitted.get("step_id") if isinstance(admitted, Mapping) else None
    if isinstance(candidate, str) and candidate:
        return candidate
    return f"command:{receipt.command_id}"


__all__ = [
    "NATIVE_COMMAND_EVENT_TYPE",
    "NativeBindingFinalizationObserver",
    "NativeCommandTransitionIntentFactory",
    "NativeTransitionPublicProjector",
    "WorkflowBindingStatusReadPort",
    "WorkflowTransitionDriveReport",
    "WorkflowTransitionDriver",
    "WorkflowTransitionNativeCompositionError",
    "build_native_transition_effect_registry",
    "build_native_transition_finalization_registry",
]
