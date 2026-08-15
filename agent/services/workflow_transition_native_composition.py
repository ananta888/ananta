"""Native command-transition cutover composition.

This module turns the previously unwired transition track into a runnable
Native slice.  It contributes the pieces the track deliberately left open: a
concrete command planner, the exact effect and finalization registries, an
authoritative binding observer, a public projector and a bounded drain driver.

What a plan contains is a deliberate choice rather than a default.  Every
transition reserves exactly one execution owner, appends exactly one Hub-owned
event and finalizes against authoritative runtime status.  Beyond that, a
queue slot is reserved only by a plan that hands work to a worker, and a
checkpoint binding is planned only where the runtime actually checkpoints — a
control command like pause does neither.

Effect kinds are likewise registered only when their authority is supplied.
An effect kind that resolves is an effect kind that can run, so a deployment
that issues no grants carries no resolvable grant executor.
"""

from __future__ import annotations

import hashlib
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, final, runtime_checkable

from agent.services.workflow_command_transition_admission import (
    WorkflowCommandTransitionAdmissionPort,
    WorkflowCommandTransitionAdmissionService,
    WorkflowCommandTransitionIntent,
)
from agent.services.workflow_control_bindings import WorkflowControlRunBinding
from agent.services.workflow_control_command_receipts import WorkflowControlCommandReceipt
from agent.services.workflow_runtime._serialization import canonical_json
from agent.services.workflow_runtime.events import (
    WorkflowTransitionEventObservationReadPort,
)
from agent.services.workflow_runtime.queue_reservations import (
    WorkflowTransitionQueueReservationAuthority,
)
from agent.services.workflow_runtime.sqlalchemy_event_stores import SQLAlchemyEventStore
from agent.services.workflow_runtime.sqlalchemy_ownership import SQLAlchemyExecutionOwnershipStore
from agent.services.workflow_runtime.sqlalchemy_queue_reservations import (
    SQLAlchemyWorkflowTransitionQueueReservationStore,
)
from agent.services.workflow_transition_authorization_grant import (
    WorkflowTransitionAuthorizationGrantExecutor,
    WorkflowTransitionAuthorizationGrantObserver,
    build_workflow_transition_authorization_grant_effect,
)
from agent.services.workflow_transition_checkpoint_binding import (
    WorkflowTransitionCheckpointBindingExecutor,
    WorkflowTransitionCheckpointBindingObserver,
    build_workflow_transition_checkpoint_binding_effect,
)
from agent.services.workflow_transition_effect_execution import (
    BoundedWorkflowTransitionRetryPolicy,
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
    EFFECT_AUTHORIZATION_GRANT,
    EFFECT_BINDING_FINALIZE,
    EFFECT_CHECKPOINT_SAVE,
    EFFECT_EVENT_APPEND,
    EFFECT_OWNERSHIP_RESERVE,
    EFFECT_QUEUE_RESERVE,
    TRANSITION_KIND_COMMAND,
    TRANSITION_RUNTIME_NATIVE,
    TRANSITION_RUNTIMES,
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
from agent.services.workflow_transition_persistence import SQLAlchemyWorkflowTransitionStore
from agent.services.workflow_transition_queue_reservation import (
    WorkflowTransitionQueueReservationExecutor,
    WorkflowTransitionQueueReservationObserver,
    build_workflow_transition_queue_reservation_effect,
)
from agent.services.workflow_transition_runner import WorkflowTransitionRunner

NATIVE_COMMAND_EVENT_TYPE = "workflow.command.admitted"

_OWNERSHIP_ORDINAL = 1
_QUEUE_ORDINAL = 2
_EVENT_ORDINAL = 3
_FINALIZE_ORDINAL = 4
_MAX_DRAIN_LIMIT = 256


class WorkflowTransitionNativeCompositionError(RuntimeError):
    """Stable fail-closed Native cutover composition error."""


@runtime_checkable
class WorkflowBindingStatusReadPort(Protocol):
    """Read authoritative runtime status for one workflow binding."""

    def get_workflow_status(self, workflow_id: str) -> Mapping[str, Any]: ...


@final
@dataclass(frozen=True, slots=True)
class PlannedAuthorizationGrant:
    """A grant derived from the plan, together with the key that signs it.

    Both halves are required.  A signing key without a derived scope could
    sign anything; a derived scope without a key could not be proven at all.
    """

    grant: Any
    signing_key_ring: Any

    def __post_init__(self) -> None:
        for name in ("allowed_tools", "allowed_artifacts", "budgets", "ttl_seconds"):
            if not hasattr(self.grant, name):
                raise WorkflowTransitionNativeCompositionError("workflow_transition_native_grant_plan_invalid")
        if not callable(getattr(self.signing_key_ring, "sign", None)):
            raise WorkflowTransitionNativeCompositionError("workflow_transition_native_signing_key_ring_invalid")


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

    __slots__ = (
        "_authorization",
        "_binds_checkpoint_revision",
        "_events",
        "_lease_seconds",
        "_maximum_retries",
        "_reserves_queue_slot",
        "_runtime_id",
        "_transitions",
    )

    def __init__(
        self,
        *,
        events: WorkflowTransitionEventObservationReadPort,
        transitions: WorkflowTransitionReadPort,
        lease_seconds: float = 30.0,
        maximum_retries: int = 3,
        runtime_id: str = TRANSITION_RUNTIME_NATIVE,
        reserves_queue_slot: bool = False,
        binds_checkpoint_revision: int | None = None,
        authorization: PlannedAuthorizationGrant | None = None,
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
        if runtime_id not in TRANSITION_RUNTIMES:
            raise WorkflowTransitionNativeCompositionError("workflow_transition_native_runtime_invalid")
        if not isinstance(reserves_queue_slot, bool):
            raise WorkflowTransitionNativeCompositionError("workflow_transition_native_queue_flag_invalid")
        self._lease_seconds = float(lease_seconds)
        self._maximum_retries = int(maximum_retries)
        if binds_checkpoint_revision is not None and (
            isinstance(binds_checkpoint_revision, bool)
            or not isinstance(binds_checkpoint_revision, int)
            or binds_checkpoint_revision <= 0
        ):
            raise WorkflowTransitionNativeCompositionError("workflow_transition_native_checkpoint_revision_invalid")
        self._runtime_id = runtime_id
        self._reserves_queue_slot = reserves_queue_slot
        self._binds_checkpoint_revision = binds_checkpoint_revision
        if authorization is not None and not isinstance(authorization, PlannedAuthorizationGrant):
            raise WorkflowTransitionNativeCompositionError("workflow_transition_native_authorization_invalid")
        self._authorization = authorization

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
        scope = {
            "transition_id": transition_id,
            "tenant_id": binding.tenant_id,
            "workflow_id": binding.workflow_id,
            "run_id": binding.run_id,
            "runtime_id": self._runtime_id,
            "step_id": step_id,
        }
        # Ordinals are handed out in plan order rather than fixed per kind,
        # because which effects a plan contains is a per-plan decision.
        ordinal = _OWNERSHIP_ORDINAL
        ownership = build_workflow_transition_ownership_reservation_effect(
            **scope,
            ordinal=ordinal,
            lease_seconds=self._lease_seconds,
            maximum_retries=self._maximum_retries,
            planned_at=planned_at,
        )
        grant: tuple[WorkflowTransitionEffect, ...] = ()
        if self._authorization is not None:
            # A Hub-owned grant intent precedes any ingest, so nothing reaches
            # a worker before the Hub has said what that work may do.
            ordinal += 1
            grant = (
                build_workflow_transition_authorization_grant_effect(
                    **scope,
                    signing_key_ring=self._authorization.signing_key_ring,
                    ordinal=ordinal,
                    plan_hash=binding.plan_hash,
                    policy_version=binding.policy_version,
                    allowed_tools=self._authorization.grant.allowed_tools,
                    allowed_artifacts=self._authorization.grant.allowed_artifacts,
                    budgets=self._authorization.grant.budgets,
                    ttl_seconds=self._authorization.grant.ttl_seconds,
                    planned_at=planned_at,
                ),
            )
        queue: tuple[WorkflowTransitionEffect, ...] = ()
        if self._reserves_queue_slot:
            # The reservation is the ingest: a run that hands work to a worker
            # must own exactly one slot before any event says it did.
            ordinal += 1
            queue = (
                build_workflow_transition_queue_reservation_effect(
                    **scope,
                    ordinal=ordinal,
                    task_id=workflow_transition_task_id(transition_id=transition_id),
                    maximum_retries=self._maximum_retries,
                    planned_at=planned_at,
                ),
            )
        checkpoint: tuple[WorkflowTransitionEffect, ...] = ()
        if self._binds_checkpoint_revision is not None:
            ordinal += 1
            checkpoint = (
                build_workflow_transition_checkpoint_binding_effect(
                    **scope,
                    ordinal=ordinal,
                    task_id=workflow_transition_task_id(transition_id=transition_id),
                    expected_revision=self._binds_checkpoint_revision,
                    planned_at=planned_at,
                ),
            )
        event_ordinal = ordinal + 1
        event = build_workflow_transition_event_effect(
            transition_id=transition_id,
            tenant_id=binding.tenant_id,
            workflow_id=binding.workflow_id,
            run_id=binding.run_id,
            ordinal=event_ordinal,
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
                ordinal=event_ordinal,
            ),
            planned_at=planned_at,
        )
        finalize = WorkflowTransitionEffect.build(
            transition_id=transition_id,
            ordinal=event_ordinal + 1,
            kind=EFFECT_BINDING_FINALIZE,
            idempotency_key=binding.workflow_id,
            payload={"workflow_id": binding.workflow_id},
            created_at=planned_at,
        )
        effects = (ownership, *grant, *queue, *checkpoint, event, finalize)
        transition = WorkflowTransition.build(
            transition_id=transition_id,
            tenant_id=binding.tenant_id,
            workflow_id=binding.workflow_id,
            run_id=binding.run_id,
            runtime_id=self._runtime_id,
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
        ordinal: int,
    ) -> int:
        dedupe_key = workflow_transition_event_effect_idempotency_key(
            transition_id=transition_id,
            ordinal=ordinal,
            event_type=NATIVE_COMMAND_EVENT_TYPE,
        )
        effect_id = workflow_transition_effect_id(
            transition_id=transition_id,
            ordinal=ordinal,
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


@final
@dataclass(frozen=True, slots=True)
class NativeCheckpointBindingWiring:
    """The binding authority together with the checkpoints it reads.

    Both are required: a binding authority without the checkpoint store it
    derives digests from could only record a caller's claim about a revision,
    which is exactly the unverified evidence the binding exists to replace.
    """

    authority: Any
    checkpoints: Any

    def __post_init__(self) -> None:
        if not callable(getattr(self.authority, "bind_transition_checkpoint", None)):
            raise WorkflowTransitionNativeCompositionError("workflow_transition_native_checkpoint_authority_invalid")
        if not callable(getattr(self.checkpoints, "get_latest", None)):
            raise WorkflowTransitionNativeCompositionError("workflow_transition_native_checkpoint_reads_invalid")


@final
@dataclass(frozen=True, slots=True)
class NativeAuthorizationGrantWiring:
    """Everything the grant effect needs, or nothing at all.

    Historical integrity and current authority stay two distinct verifiers on
    purpose.  The retained-key verifier proves only that an existing issuance
    was signed, so it must never authorize a new grant; the current verifier is
    revocation aware and is the one consulted immediately before the commit.
    Bundling them means a deployment cannot accidentally configure only the
    permissive half.
    """

    authority: Any
    historical_integrity: Any
    current_verifier: Any

    def __post_init__(self) -> None:
        historical = getattr(self.historical_integrity, "signature_algorithm", None)
        current = getattr(self.current_verifier, "signature_algorithm", None)
        if historical != current or not isinstance(historical, str) or not historical:
            raise WorkflowTransitionNativeCompositionError("workflow_transition_native_grant_algorithm_mismatch")
        if self.historical_integrity is self.current_verifier:
            raise WorkflowTransitionNativeCompositionError("workflow_transition_native_grant_verifier_shared")


def build_native_transition_effect_registry(
    *,
    ownership_authority: WorkflowTransitionOwnershipReservationAuthority,
    ownership_reads: WorkflowTransitionOwnershipReservationObserverReads,
    event_authority: WorkflowTransitionEventAuthority,
    event_reads: WorkflowTransitionEventObservationReadPort,
    queue_reservations: WorkflowTransitionQueueReservationAuthority,
    clock: Callable[[], float],
    runtime_id: str = TRANSITION_RUNTIME_NATIVE,
    checkpoint_bindings: NativeCheckpointBindingWiring | None = None,
    authorization_grants: NativeAuthorizationGrantWiring | None = None,
) -> WorkflowTransitionEffectExecutorRegistry:
    """Register exactly the Native effect kinds that have a proven adapter.

    The authorization grant is opt in.  A deployment that never issues grants
    should not carry a resolvable grant executor, because an effect kind that
    resolves is an effect kind that can run.
    """

    if not callable(clock):
        raise WorkflowTransitionNativeCompositionError("workflow_transition_native_clock_invalid")
    if runtime_id not in TRANSITION_RUNTIMES:
        raise WorkflowTransitionNativeCompositionError("workflow_transition_native_runtime_invalid")
    checkpoint_registrations: tuple[WorkflowTransitionEffectRegistration, ...] = ()
    if checkpoint_bindings is not None:
        checkpoint_registrations = (
            WorkflowTransitionEffectRegistration(
                runtime_id=runtime_id,
                effect_kind=EFFECT_CHECKPOINT_SAVE,
                handler=WorkflowTransitionEffectHandler(
                    observation=WorkflowTransitionCheckpointBindingObserver(reads=checkpoint_bindings.authority),
                    execution=WorkflowTransitionCheckpointBindingExecutor(
                        authority=checkpoint_bindings.authority,
                        checkpoints=checkpoint_bindings.checkpoints,
                        clock=clock,
                    ),
                ),
            ),
        )
    grant_registrations: tuple[WorkflowTransitionEffectRegistration, ...] = ()
    if authorization_grants is not None:
        grant_registrations = (
            WorkflowTransitionEffectRegistration(
                runtime_id=runtime_id,
                effect_kind=EFFECT_AUTHORIZATION_GRANT,
                handler=WorkflowTransitionEffectHandler(
                    observation=WorkflowTransitionAuthorizationGrantObserver(
                        reads=authorization_grants.authority,
                        historical_integrity=authorization_grants.historical_integrity,
                        current_verifier=authorization_grants.current_verifier,
                        clock=clock,
                    ),
                    execution=WorkflowTransitionAuthorizationGrantExecutor(
                        authority=authorization_grants.authority,
                        historical_integrity=authorization_grants.historical_integrity,
                        current_verifier=authorization_grants.current_verifier,
                        clock=clock,
                    ),
                ),
            ),
        )
    return WorkflowTransitionEffectExecutorRegistry(
        (
            *grant_registrations,
            WorkflowTransitionEffectRegistration(
                runtime_id=runtime_id,
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
            *checkpoint_registrations,
            WorkflowTransitionEffectRegistration(
                runtime_id=runtime_id,
                effect_kind=EFFECT_QUEUE_RESERVE,
                handler=WorkflowTransitionEffectHandler(
                    observation=WorkflowTransitionQueueReservationObserver(reads=queue_reservations),
                    execution=WorkflowTransitionQueueReservationExecutor(
                        authority=queue_reservations,
                        clock=clock,
                    ),
                ),
            ),
            WorkflowTransitionEffectRegistration(
                runtime_id=runtime_id,
                effect_kind=EFFECT_EVENT_APPEND,
                handler=WorkflowTransitionEffectHandler(
                    observation=WorkflowTransitionEventEffectObserver(
                        runtime_id=runtime_id,
                        reads=event_reads,
                    ),
                    execution=WorkflowTransitionEventEffectExecutor(
                        runtime_id=runtime_id,
                        authority=event_authority,
                    ),
                ),
            ),
        )
    )


def build_native_transition_finalization_registry(
    *,
    status_reads: WorkflowBindingStatusReadPort,
    runtime_id: str = TRANSITION_RUNTIME_NATIVE,
) -> WorkflowTransitionFinalizationObserverRegistry:
    """Register the authoritative Native command finalization observer."""

    return WorkflowTransitionFinalizationObserverRegistry(
        (
            WorkflowTransitionFinalizationRegistration(
                runtime_id=runtime_id,
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


@final
@dataclass(frozen=True, slots=True)
class WorkflowCommandTransitionRuntime:
    """The two capabilities the control facade needs, bundled as one seam.

    Passing a single collaborator keeps the transition path an all-or-nothing
    decision: a deployment either admits commands as transitions and drives
    them, or does neither.  A half-configured control path could stage a
    transition nothing would ever run.
    """

    admission: WorkflowCommandTransitionAdmissionPort
    driver: WorkflowTransitionDriver

    def __post_init__(self) -> None:
        if not callable(getattr(self.admission, "stage_or_adopt", None)):
            raise WorkflowTransitionNativeCompositionError("workflow_transition_native_admission_invalid")
        if not isinstance(self.driver, WorkflowTransitionDriver):
            raise WorkflowTransitionNativeCompositionError("workflow_transition_native_driver_invalid")


def build_native_command_transition_runtime(
    bind: Any,
    *,
    status_reads: WorkflowBindingStatusReadPort,
    owner_id: str,
    clock: Callable[[], float] = time.time,
    lease_seconds: float = 30.0,
    maximum_attempts: int = 3,
    drain_limit: int = 32,
) -> WorkflowCommandTransitionRuntime:
    """Assemble the whole Native transition path against one database bind.

    Every store here writes through the same engine, so the transition, its
    binding and its command receipt stay in one transactional world.  The
    command receipt in particular is not a second record: the transition store
    updates the very row the control receipt store created, under CAS.
    """

    transitions = SQLAlchemyWorkflowTransitionStore(
        bind,
        clock=clock,
        receipt_projector=NativeTransitionPublicProjector(),
    )
    ownership = SQLAlchemyExecutionOwnershipStore(bind)
    events = SQLAlchemyEventStore(bind)
    queue_reservations = SQLAlchemyWorkflowTransitionQueueReservationStore(bind)
    admission = WorkflowCommandTransitionAdmissionService(
        transitions,
        transition_reader=transitions,
        intent_factory=NativeCommandTransitionIntentFactory(
            events=events,
            transitions=transitions,
            lease_seconds=lease_seconds,
            maximum_retries=maximum_attempts,
        ),
        clock=clock,
    )
    runner = WorkflowTransitionRunner(
        reads=transitions,
        leases=transitions,
        effects=transitions,
        completion=transitions,
        quarantine=transitions,
        effect_registry=build_native_transition_effect_registry(
            ownership_authority=ownership,
            ownership_reads=ownership,
            event_authority=events,
            event_reads=events,
            queue_reservations=queue_reservations,
            clock=clock,
        ),
        finalization_registry=build_native_transition_finalization_registry(status_reads=status_reads),
        retry_policy=BoundedWorkflowTransitionRetryPolicy(maximum_attempts, 2.0, 2.0, 10.0),
        owner_id=owner_id,
        lease_seconds=lease_seconds,
        clock=clock,
    )
    return WorkflowCommandTransitionRuntime(
        admission=admission,
        driver=WorkflowTransitionDriver(runner=runner, limit=drain_limit),
    )


def workflow_transition_task_id(*, transition_id: str) -> str:
    """Derive the one task a transition may reserve, from the transition alone.

    Deriving rather than allocating is what makes the reservation restart
    safe: a replanned or retried transition names the same task instead of
    asking the queue for a second one.
    """

    if not isinstance(transition_id, str) or not transition_id:
        raise WorkflowTransitionNativeCompositionError("workflow_transition_native_transition_id_invalid")
    framed = canonical_json({"namespace": "workflow-transition-task-id.v1", "transition_id": transition_id})
    return f"wftt-{hashlib.sha256(framed.encode('utf-8')).hexdigest()[:40]}"


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
    "NativeAuthorizationGrantWiring",
    "NativeCheckpointBindingWiring",
    "PlannedAuthorizationGrant",
    "NativeCommandTransitionIntentFactory",
    "NativeTransitionPublicProjector",
    "WorkflowBindingStatusReadPort",
    "WorkflowCommandTransitionRuntime",
    "WorkflowTransitionDriveReport",
    "WorkflowTransitionDriver",
    "WorkflowTransitionNativeCompositionError",
    "workflow_transition_task_id",
    "build_native_command_transition_runtime",
    "build_native_transition_effect_registry",
    "build_native_transition_finalization_registry",
]
