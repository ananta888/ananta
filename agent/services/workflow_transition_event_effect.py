"""Unwired event-append observer/executor for Hub transition effects.

The adapter is deliberately absent from production composition.  It accepts
only the segregated atomic observation and transition-append capabilities,
never a telemetry-decorated event sink, and invents no identifiers or time.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, final, runtime_checkable

from agent.services.workflow_runtime._serialization import canonical_json, redact_json
from agent.services.workflow_runtime.events import (
    CANONICAL_WORKFLOW_EVENT_SCHEMA,
    WORKFLOW_EVENT_COMMIT_MODES,
    CanonicalWorkflowEvent,
    WorkflowEventCommitProof,
    WorkflowEventIdentityHeadSnapshot,
    WorkflowTransitionEventAppendPort,
    WorkflowTransitionEventObservationReadPort,
    event_payload_equal,
    workflow_transition_event_payload_copy,
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
    assert_active_workflow_transition_effect_proof_binding,
    assert_durable_workflow_transition_effect_proof_binding,
    workflow_transition_effect_resource_digest,
)
from agent.services.workflow_transition_outbox import (
    EFFECT_EVENT_APPEND,
    TRANSITION_RUNTIMES,
    WorkflowTransition,
    WorkflowTransitionEffect,
    workflow_transition_effect_id,
)

WORKFLOW_TRANSITION_EVENT_EFFECT_SCHEMA = "ananta.workflow_transition_event_effect.v1"
WORKFLOW_TRANSITION_EVENT_RESULT_SCHEMA = "ananta.workflow_transition_event_result.v1"
WORKFLOW_TRANSITION_EVENT_OBSERVATION_SCHEMA = "ananta.workflow_transition_event_observation.v1"
WORKFLOW_TRANSITION_EVENT_ACTOR = "ananta-hub"
WORKFLOW_TRANSITION_EVENT_RESOURCE_KIND = "workflow_event"
WORKFLOW_TRANSITION_EVENT_SLOT_KIND = "workflow_event_append_slot"

_EFFECT_PAYLOAD_FIELDS = frozenset(
    {
        "schema",
        "expected_sequence",
        "event_payload_digest",
        "event_content_digest",
        "event",
    }
)
_EVENT_FIELDS = frozenset(
    {
        "schema",
        "event_id",
        "tenant_id",
        "workflow_id",
        "run_id",
        "step_id",
        "attempt",
        "event_type",
        "actor",
        "correlation_id",
        "causation_id",
        "sequence",
        "dedupe_key",
        "occurred_at",
        "payload",
    }
)
_MAX_EVENT_BYTES = 240_000
_MAX_TEXT_CHARS = 256
_MAX_SEQUENCE = 2**63 - 1


class WorkflowTransitionEventEffectError(ValueError):
    """Stable fail-closed staged-event or adapter contract error."""


_SCALARS = WorkflowTransitionEffectScalars(
    error=WorkflowTransitionEventEffectError,
    prefix="workflow_transition_event",
)


@runtime_checkable
class WorkflowTransitionEventAuthority(
    WorkflowTransitionEventObservationReadPort,
    WorkflowTransitionEventAppendPort,
    Protocol,
):
    """Combined raw authority required by the mutating executor."""


@final
@dataclass(frozen=True, slots=True)
class _EventIntent:
    event: CanonicalWorkflowEvent
    expected_sequence: int
    event_payload_digest: str
    event_content_digest: str


@final
@dataclass(frozen=True, slots=True)
class _ObservedEvent:
    event: CanonicalWorkflowEvent
    commit: WorkflowEventCommitProof


def workflow_transition_event_effect_idempotency_key(
    *,
    transition_id: str,
    ordinal: int,
    event_type: str,
) -> str:
    """Derive the staged dedupe/idempotency key without a fixpoint."""

    transition = _identity(transition_id, reason="transition_id")
    position = _positive_integer(ordinal, reason="ordinal")
    semantic_type = _text(event_type, reason="event_type")
    return _opaque_identifier(
        "wftei",
        "workflow-transition-event-idempotency.v1",
        transition,
        str(position),
        semantic_type,
    )


def workflow_transition_event_id(
    *,
    transition_id: str,
    effect_id: str,
) -> str:
    """Derive the event identity after the transition effect ID is known."""

    return _opaque_identifier(
        "wfte",
        "workflow-transition-event-id.v1",
        _identity(transition_id, reason="transition_id"),
        _identity(effect_id, reason="effect_id"),
    )


def build_workflow_transition_event_effect(
    *,
    transition_id: str,
    tenant_id: str,
    workflow_id: str,
    run_id: str,
    ordinal: int,
    event_type: str,
    step_id: str,
    payload: Mapping[str, Any],
    expected_sequence: int,
    planned_at: float,
) -> WorkflowTransitionEffect:
    """Build one byte-deterministic immutable event-append effect."""

    try:
        return _build_workflow_transition_event_effect(
            transition_id=transition_id,
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            run_id=run_id,
            ordinal=ordinal,
            event_type=event_type,
            step_id=step_id,
            payload=payload,
            expected_sequence=expected_sequence,
            planned_at=planned_at,
        )
    except WorkflowTransitionEventEffectError:
        raise
    except Exception as exc:
        raise WorkflowTransitionEventEffectError("workflow_transition_event_effect_payload_invalid") from exc


def _build_workflow_transition_event_effect(
    *,
    transition_id: str,
    tenant_id: str,
    workflow_id: str,
    run_id: str,
    ordinal: int,
    event_type: str,
    step_id: str,
    payload: Mapping[str, Any],
    expected_sequence: int,
    planned_at: float,
) -> WorkflowTransitionEffect:
    transition = _identity(transition_id, reason="transition_id")
    tenant = _identity(tenant_id, reason="tenant_id")
    workflow = _identity(workflow_id, reason="workflow_id")
    run = _identity(run_id, reason="run_id")
    position = _positive_integer(ordinal, reason="ordinal")
    semantic_type = _text(event_type, reason="event_type")
    step = _optional_text(step_id, reason="step_id")
    sequence = _nonnegative_integer(expected_sequence, reason="expected_sequence")
    occurred_at = _positive_timestamp(planned_at, reason="planned_at")
    safe_payload = _json_mapping(payload, reason="event_payload", empty=True)
    if canonical_json(redact_json(safe_payload)) != canonical_json(safe_payload):
        raise WorkflowTransitionEventEffectError("workflow_transition_event_payload_sensitive")

    idempotency_key = workflow_transition_event_effect_idempotency_key(
        transition_id=transition,
        ordinal=position,
        event_type=semantic_type,
    )
    effect_id = workflow_transition_effect_id(
        transition_id=transition,
        ordinal=position,
        kind=EFFECT_EVENT_APPEND,
        idempotency_key=idempotency_key,
    )
    event = CanonicalWorkflowEvent(
        tenant_id=tenant,
        workflow_id=workflow,
        run_id=run,
        event_type=semantic_type,
        correlation_id=transition,
        causation_id=effect_id,
        dedupe_key=idempotency_key,
        sequence=0,
        step_id=step,
        attempt=0,
        actor=WORKFLOW_TRANSITION_EVENT_ACTOR,
        occurred_at=occurred_at,
        payload=safe_payload,
        event_id=workflow_transition_event_id(
            transition_id=transition,
            effect_id=effect_id,
        ),
    )
    event.assert_valid(allow_unsequenced=True)
    event_mapping = event.to_dict()
    _bounded_event(event_mapping)
    try:
        event_payload_digest = _event_payload_digest(event)
        event_content_digest = _event_content_digest(event)
    except Exception as exc:
        raise WorkflowTransitionEventEffectError("workflow_transition_event_payload_invalid") from exc
    effect_payload = {
        "schema": WORKFLOW_TRANSITION_EVENT_EFFECT_SCHEMA,
        "expected_sequence": sequence,
        "event_payload_digest": event_payload_digest,
        "event_content_digest": event_content_digest,
        "event": event_mapping,
    }
    effect_payload = _json_mapping(
        effect_payload,
        reason="event_effect_payload",
        empty=False,
    )
    effect = WorkflowTransitionEffect.build(
        transition_id=transition,
        ordinal=position,
        kind=EFFECT_EVENT_APPEND,
        idempotency_key=idempotency_key,
        payload=effect_payload,
        created_at=occurred_at,
    )
    if effect.effect_id != effect_id:  # pragma: no cover - defensive against helper drift
        raise WorkflowTransitionEventEffectError("workflow_transition_event_effect_id_conflict")
    return effect


def workflow_transition_event_observation_digest(
    snapshot: WorkflowEventIdentityHeadSnapshot,
) -> str:
    """Digest the complete atomic identity/head/commit observation."""

    if not isinstance(snapshot, WorkflowEventIdentityHeadSnapshot):
        raise WorkflowTransitionEventEffectError("workflow_transition_event_observation_invalid")
    projection = {
        "schema": WORKFLOW_TRANSITION_EVENT_OBSERVATION_SCHEMA,
        "binding": {
            "tenant_id": snapshot.tenant_id,
            "workflow_id": snapshot.workflow_id,
            "run_id": snapshot.run_id,
            "dedupe_key": snapshot.dedupe_key,
            "event_id": snapshot.event_id,
            "delivery_mode": snapshot.delivery_mode,
        },
        "dedupe_event": _event_mapping(snapshot.dedupe_event),
        "event_id_event": _event_mapping(snapshot.event_id_event),
        "head_event": _event_mapping(snapshot.head_event),
        "dedupe_commit": _commit_mapping(snapshot.dedupe_commit),
        "event_id_commit": _commit_mapping(snapshot.event_id_commit),
    }
    return workflow_transition_effect_resource_digest(projection)


def assert_active_workflow_transition_event_proof(
    proof: WorkflowTransitionEffectResourceProof | Mapping[str, Any],
    *,
    transition: WorkflowTransition,
    effect: WorkflowTransitionEffect,
    claim_generation: int,
    event: CanonicalWorkflowEvent,
    commit: WorkflowEventCommitProof,
) -> WorkflowTransitionEffectResourceProof:
    """Revalidate an active event proof against one authoritative reread."""

    intent = _event_intent(transition=transition, effect=effect)
    _assert_stored_event(intent=intent, event=event, commit=commit)
    return assert_active_workflow_transition_effect_proof_binding(
        proof,
        transition=transition,
        effect=effect,
        claim_generation=claim_generation,
        resource_kind=WORKFLOW_TRANSITION_EVENT_RESOURCE_KIND,
        resource_id=event.event_id,
        resource_revision=event.sequence,
        resource_digest=_stored_event_digest(event, commit),
    )


def assert_durable_workflow_transition_event_proof(
    proof: WorkflowTransitionEffectResourceProof | Mapping[str, Any],
    *,
    transition: WorkflowTransition,
    effect: WorkflowTransitionEffect,
    event: CanonicalWorkflowEvent,
    commit: WorkflowEventCommitProof,
) -> WorkflowTransitionEffectResourceProof:
    """Revalidate persisted event evidence using the applied generation."""

    intent = _event_intent(transition=transition, effect=effect)
    _assert_stored_event(intent=intent, event=event, commit=commit)
    return assert_durable_workflow_transition_effect_proof_binding(
        proof,
        transition=transition,
        effect=effect,
        resource_kind=WORKFLOW_TRANSITION_EVENT_RESOURCE_KIND,
        resource_id=event.event_id,
        resource_revision=event.sequence,
        resource_digest=_stored_event_digest(event, commit),
    )


@final
class WorkflowTransitionEventEffectObserver:
    """Read-only exact observer; it never heartbeats, exports, or appends."""

    def __init__(
        self,
        *,
        runtime_id: str,
        reads: WorkflowTransitionEventObservationReadPort,
    ) -> None:
        if runtime_id not in TRANSITION_RUNTIMES or not callable(getattr(reads, "observe_transition_event", None)):
            raise WorkflowTransitionEventEffectError("workflow_transition_event_observer_invalid")
        self._runtime_id = runtime_id
        self._reads = reads

    def observe_or_adopt(
        self,
        observation: WorkflowTransitionEffectObservation,
        *,
        heartbeat: WorkflowTransitionHeartbeatContext,
    ) -> EffectAlreadyApplied | EffectExecutable | EffectQuarantine:
        del heartbeat
        try:
            if type(observation) is not WorkflowTransitionEffectObservation:
                raise WorkflowTransitionEventEffectError("workflow_transition_event_observation_invalid")
            intent = _event_intent(
                transition=observation.transition,
                effect=observation.effect,
                runtime_id=self._runtime_id,
            )
            snapshot = _observe(self._reads, intent)
            stored = _classify_snapshot(snapshot=snapshot, intent=intent)
            if stored is not None:
                return _already_applied(
                    observation=observation,
                    intent=intent,
                    stored=stored,
                )
            if snapshot.head_sequence != intent.expected_sequence:
                raise WorkflowTransitionEventEffectError("workflow_transition_event_sequence_drift")
            proof = WorkflowTransitionEffectAbsenceProof(
                context=WorkflowTransitionEffectProofContext.from_active_claim(
                    transition=observation.transition,
                    effect=observation.effect,
                    claim_generation=observation.claim_generation,
                ),
                resource_kind=WORKFLOW_TRANSITION_EVENT_SLOT_KIND,
                resource_id=intent.event.event_id,
                head_revision=snapshot.head_sequence,
                head_digest=workflow_transition_event_observation_digest(snapshot),
            )
            return EffectExecutable(proof.to_dict())
        except Exception:
            return EffectQuarantine("event_append_observation_conflict")


@final
class WorkflowTransitionEventEffectExecutor:
    """Append through one raw authority and prove the committed exact reread."""

    def __init__(
        self,
        *,
        runtime_id: str,
        authority: WorkflowTransitionEventAuthority,
    ) -> None:
        if runtime_id not in TRANSITION_RUNTIMES or not isinstance(
            authority,
            WorkflowTransitionEventAuthority,
        ):
            raise WorkflowTransitionEventEffectError("workflow_transition_event_executor_invalid")
        self._runtime_id = runtime_id
        self._authority = authority

    def execute(
        self,
        attempt: WorkflowTransitionEffectAttempt,
        *,
        executable: EffectExecutable,
        heartbeat: WorkflowTransitionHeartbeatContext,
    ) -> EffectApplied | EffectRetry | EffectQuarantine:
        del heartbeat
        try:
            if type(attempt) is not WorkflowTransitionEffectAttempt or type(executable) is not EffectExecutable:
                raise WorkflowTransitionEventEffectError("workflow_transition_event_attempt_invalid")
            intent = _event_intent(
                transition=attempt.transition,
                effect=attempt.effect,
                runtime_id=self._runtime_id,
            )
            absence = WorkflowTransitionEffectAbsenceProof.from_mapping(executable.proof_payload)
            assert_active_workflow_transition_effect_absence_proof_binding(
                absence,
                transition=attempt.transition,
                effect=attempt.effect,
                claim_generation=attempt.claim_generation,
                resource_kind=WORKFLOW_TRANSITION_EVENT_SLOT_KIND,
                resource_id=intent.event.event_id,
                head_revision=absence.head_revision,
                head_digest=absence.head_digest,
            )
            before = _observe(self._authority, intent)
            stored = _classify_snapshot(snapshot=before, intent=intent)
            if stored is not None:
                return _applied(attempt=attempt, intent=intent, stored=stored)
            _assert_executable_absence(
                proof=absence,
                snapshot=before,
                intent=intent,
                transition=attempt.transition,
                effect=attempt.effect,
                claim_generation=attempt.claim_generation,
            )
        except Exception:
            return EffectQuarantine("event_append_executable_proof_invalid")

        try:
            self._authority.append_transition_event(
                intent.event,
                expected_sequence=intent.expected_sequence,
            )
        except Exception:
            return self._after_append_exception(
                attempt=attempt,
                intent=intent,
                absence=absence,
            )

        try:
            after = _observe(self._authority, intent)
            stored = _classify_snapshot(snapshot=after, intent=intent)
            if stored is None:
                return EffectQuarantine("event_append_commit_missing")
            return _applied(attempt=attempt, intent=intent, stored=stored)
        except Exception:
            return EffectQuarantine("event_append_commit_conflict")

    def _after_append_exception(
        self,
        *,
        attempt: WorkflowTransitionEffectAttempt,
        intent: _EventIntent,
        absence: WorkflowTransitionEffectAbsenceProof,
    ) -> EffectApplied | EffectRetry | EffectQuarantine:
        try:
            after = _observe(self._authority, intent)
            stored = _classify_snapshot(snapshot=after, intent=intent)
            if stored is not None:
                return _applied(attempt=attempt, intent=intent, stored=stored)
            _assert_executable_absence(
                proof=absence,
                snapshot=after,
                intent=intent,
                transition=attempt.transition,
                effect=attempt.effect,
                claim_generation=attempt.claim_generation,
            )
            return EffectRetry("event_append_unconfirmed")
        except Exception:
            return EffectQuarantine("event_append_commit_conflict")


def _event_intent(
    *,
    transition: WorkflowTransition,
    effect: WorkflowTransitionEffect,
    runtime_id: str | None = None,
) -> _EventIntent:
    try:
        if (
            not isinstance(transition, WorkflowTransition)
            or not isinstance(effect, WorkflowTransitionEffect)
            or effect.transition_id != transition.transition_id
            or effect.kind != EFFECT_EVENT_APPEND
            or effect.created_at != transition.created_at
            or (runtime_id is not None and transition.runtime_id != runtime_id)
        ):
            raise WorkflowTransitionEventEffectError("workflow_transition_event_effect_binding_invalid")
        raw = _json_mapping(effect.payload, reason="event_effect_payload", empty=False)
        if not isinstance(raw, dict) or set(raw) != _EFFECT_PAYLOAD_FIELDS:
            raise WorkflowTransitionEventEffectError("workflow_transition_event_effect_payload_invalid")
        if raw["schema"] != WORKFLOW_TRANSITION_EVENT_EFFECT_SCHEMA:
            raise WorkflowTransitionEventEffectError("workflow_transition_event_effect_schema_unsupported")
        expected_sequence = _nonnegative_integer(
            raw["expected_sequence"],
            reason="expected_sequence",
        )
        _sha256(raw["event_payload_digest"], reason="event_payload_digest")
        _sha256(raw["event_content_digest"], reason="event_content_digest")
        event = _strict_event(
            raw["event"],
            transition=transition,
            effect=effect,
        )
        payload_digest = _event_payload_digest(event)
        content_digest = _event_content_digest(event)
        if raw["event_payload_digest"] != payload_digest or raw["event_content_digest"] != content_digest:
            raise WorkflowTransitionEventEffectError("workflow_transition_event_digest_mismatch")
        return _EventIntent(
            event=event,
            expected_sequence=expected_sequence,
            event_payload_digest=payload_digest,
            event_content_digest=content_digest,
        )
    except WorkflowTransitionEventEffectError:
        raise
    except Exception as exc:
        raise WorkflowTransitionEventEffectError("workflow_transition_event_effect_payload_invalid") from exc


def _strict_event(
    raw: Any,
    *,
    transition: WorkflowTransition,
    effect: WorkflowTransitionEffect,
) -> CanonicalWorkflowEvent:
    if not isinstance(raw, Mapping) or set(raw) != _EVENT_FIELDS:
        raise WorkflowTransitionEventEffectError("workflow_transition_event_payload_invalid")
    if raw["schema"] != CANONICAL_WORKFLOW_EVENT_SCHEMA:
        raise WorkflowTransitionEventEffectError("workflow_transition_event_schema_unsupported")
    for field_name in (
        "event_id",
        "tenant_id",
        "workflow_id",
        "run_id",
        "event_type",
        "actor",
        "correlation_id",
        "causation_id",
        "dedupe_key",
    ):
        _text(raw[field_name], reason=field_name)
    _optional_text(raw["step_id"], reason="step_id")
    if _nonnegative_integer(raw["sequence"], reason="event_sequence") != 0:
        raise WorkflowTransitionEventEffectError("workflow_transition_event_sequence_invalid")
    if _nonnegative_integer(raw["attempt"], reason="event_attempt") != 0:
        raise WorkflowTransitionEventEffectError("workflow_transition_event_attempt_invalid")
    occurred_at = _positive_timestamp(raw["occurred_at"], reason="event_occurred_at")
    payload = _json_mapping(raw["payload"], reason="event_payload", empty=True)
    if canonical_json(redact_json(payload)) != canonical_json(payload):
        raise WorkflowTransitionEventEffectError("workflow_transition_event_payload_sensitive")

    expected_idempotency = workflow_transition_event_effect_idempotency_key(
        transition_id=transition.transition_id,
        ordinal=effect.ordinal,
        event_type=raw["event_type"],
    )
    expected_effect_id = workflow_transition_effect_id(
        transition_id=transition.transition_id,
        ordinal=effect.ordinal,
        kind=EFFECT_EVENT_APPEND,
        idempotency_key=expected_idempotency,
    )
    expected_event_id = workflow_transition_event_id(
        transition_id=transition.transition_id,
        effect_id=expected_effect_id,
    )
    actual_binding = (
        raw["tenant_id"],
        raw["workflow_id"],
        raw["run_id"],
        raw["dedupe_key"],
        raw["event_id"],
        raw["correlation_id"],
        raw["causation_id"],
        raw["actor"],
        occurred_at,
        effect.idempotency_key,
        effect.effect_id,
    )
    expected_binding = (
        transition.tenant_id,
        transition.workflow_id,
        transition.run_id,
        expected_idempotency,
        expected_event_id,
        transition.transition_id,
        effect.effect_id,
        WORKFLOW_TRANSITION_EVENT_ACTOR,
        float(transition.created_at),
        expected_idempotency,
        expected_effect_id,
    )
    if actual_binding != expected_binding:
        raise WorkflowTransitionEventEffectError("workflow_transition_event_binding_conflict")

    event = CanonicalWorkflowEvent(
        tenant_id=raw["tenant_id"],
        workflow_id=raw["workflow_id"],
        run_id=raw["run_id"],
        event_type=raw["event_type"],
        correlation_id=raw["correlation_id"],
        causation_id=raw["causation_id"],
        dedupe_key=raw["dedupe_key"],
        sequence=raw["sequence"],
        step_id=raw["step_id"],
        attempt=raw["attempt"],
        actor=raw["actor"],
        occurred_at=occurred_at,
        payload=payload,
        event_id=raw["event_id"],
        schema=raw["schema"],
    )
    event.assert_valid(allow_unsequenced=True)
    if canonical_json(event.to_dict()) != canonical_json(dict(raw)):
        raise WorkflowTransitionEventEffectError("workflow_transition_event_roundtrip_conflict")
    _bounded_event(event.to_dict())
    return event


def _observe(
    reads: WorkflowTransitionEventObservationReadPort,
    intent: _EventIntent,
) -> WorkflowEventIdentityHeadSnapshot:
    snapshot = reads.observe_transition_event(
        tenant_id=intent.event.tenant_id,
        workflow_id=intent.event.workflow_id,
        run_id=intent.event.run_id,
        dedupe_key=intent.event.dedupe_key,
        event_id=intent.event.event_id,
    )
    if not isinstance(snapshot, WorkflowEventIdentityHeadSnapshot):
        raise WorkflowTransitionEventEffectError("workflow_transition_event_observation_invalid")
    if (
        snapshot.tenant_id,
        snapshot.workflow_id,
        snapshot.run_id,
        snapshot.dedupe_key,
        snapshot.event_id,
    ) != (
        intent.event.tenant_id,
        intent.event.workflow_id,
        intent.event.run_id,
        intent.event.dedupe_key,
        intent.event.event_id,
    ):
        raise WorkflowTransitionEventEffectError("workflow_transition_event_observation_binding_conflict")
    return snapshot


def _classify_snapshot(
    *,
    snapshot: WorkflowEventIdentityHeadSnapshot,
    intent: _EventIntent,
) -> _ObservedEvent | None:
    dedupe_event = snapshot.dedupe_event
    identity_event = snapshot.event_id_event
    dedupe_commit = snapshot.dedupe_commit
    identity_commit = snapshot.event_id_commit
    if dedupe_event is None and identity_event is None:
        if dedupe_commit is not None or identity_commit is not None:
            raise WorkflowTransitionEventEffectError("workflow_transition_event_stray_commit")
        return None
    if dedupe_event is None or identity_event is None:
        raise WorkflowTransitionEventEffectError("workflow_transition_event_identity_conflict")
    if canonical_json(dedupe_event.to_dict()) != canonical_json(identity_event.to_dict()):
        raise WorkflowTransitionEventEffectError("workflow_transition_event_identity_conflict")
    if dedupe_commit is None or identity_commit is None or dedupe_commit != identity_commit:
        raise WorkflowTransitionEventEffectError("workflow_transition_event_commit_missing")
    _assert_stored_event(
        intent=intent,
        event=dedupe_event,
        commit=dedupe_commit,
    )
    return _ObservedEvent(event=dedupe_event, commit=dedupe_commit)


def _assert_stored_event(
    *,
    intent: _EventIntent,
    event: CanonicalWorkflowEvent,
    commit: WorkflowEventCommitProof,
) -> None:
    if not isinstance(event, CanonicalWorkflowEvent) or not isinstance(
        commit,
        WorkflowEventCommitProof,
    ):
        raise WorkflowTransitionEventEffectError("workflow_transition_event_resource_invalid")
    if event.sequence != intent.expected_sequence + 1 or not event_payload_equal(
        event,
        intent.event,
    ):
        raise WorkflowTransitionEventEffectError("workflow_transition_event_resource_conflict")
    if commit.delivery_mode not in WORKFLOW_EVENT_COMMIT_MODES:
        raise WorkflowTransitionEventEffectError("workflow_transition_event_commit_invalid")
    expected_commit = WorkflowEventCommitProof.for_event(
        event,
        delivery_mode=commit.delivery_mode,
    )
    if commit != expected_commit:
        raise WorkflowTransitionEventEffectError("workflow_transition_event_commit_conflict")


def _assert_executable_absence(
    *,
    proof: WorkflowTransitionEffectAbsenceProof,
    snapshot: WorkflowEventIdentityHeadSnapshot,
    intent: _EventIntent,
    transition: WorkflowTransition,
    effect: WorkflowTransitionEffect,
    claim_generation: int,
) -> None:
    if snapshot.head_sequence != intent.expected_sequence:
        raise WorkflowTransitionEventEffectError("workflow_transition_event_sequence_drift")
    assert_active_workflow_transition_effect_absence_proof_binding(
        proof,
        transition=transition,
        effect=effect,
        claim_generation=claim_generation,
        resource_kind=WORKFLOW_TRANSITION_EVENT_SLOT_KIND,
        resource_id=intent.event.event_id,
        head_revision=snapshot.head_sequence,
        head_digest=workflow_transition_event_observation_digest(snapshot),
    )


def _already_applied(
    *,
    observation: WorkflowTransitionEffectObservation,
    intent: _EventIntent,
    stored: _ObservedEvent,
) -> EffectAlreadyApplied:
    proof = _active_event_proof(
        transition=observation.transition,
        effect=observation.effect,
        claim_generation=observation.claim_generation,
        event=stored.event,
        commit=stored.commit,
    )
    return EffectAlreadyApplied(
        _event_result(stored.event, stored.commit),
        proof.to_dict(),
    )


def _applied(
    *,
    attempt: WorkflowTransitionEffectAttempt,
    intent: _EventIntent,
    stored: _ObservedEvent,
) -> EffectApplied:
    _assert_stored_event(intent=intent, event=stored.event, commit=stored.commit)
    proof = _active_event_proof(
        transition=attempt.transition,
        effect=attempt.effect,
        claim_generation=attempt.claim_generation,
        event=stored.event,
        commit=stored.commit,
    )
    return EffectApplied(
        _event_result(stored.event, stored.commit),
        proof.to_dict(),
    )


def _active_event_proof(
    *,
    transition: WorkflowTransition,
    effect: WorkflowTransitionEffect,
    claim_generation: int,
    event: CanonicalWorkflowEvent,
    commit: WorkflowEventCommitProof,
) -> WorkflowTransitionEffectResourceProof:
    return WorkflowTransitionEffectResourceProof(
        context=WorkflowTransitionEffectProofContext.from_active_claim(
            transition=transition,
            effect=effect,
            claim_generation=claim_generation,
        ),
        resource_kind=WORKFLOW_TRANSITION_EVENT_RESOURCE_KIND,
        resource_id=event.event_id,
        resource_revision=event.sequence,
        resource_digest=_stored_event_digest(event, commit),
    )


def _event_result(
    event: CanonicalWorkflowEvent,
    commit: WorkflowEventCommitProof,
) -> dict[str, Any]:
    return {
        "schema": WORKFLOW_TRANSITION_EVENT_RESULT_SCHEMA,
        "event": event.to_dict(),
        "commit": commit.to_dict(),
    }


def _stored_event_digest(
    event: CanonicalWorkflowEvent,
    commit: WorkflowEventCommitProof,
) -> str:
    return workflow_transition_effect_resource_digest(
        {
            "event": event.to_dict(),
            "commit": commit.to_dict(),
        }
    )


def _event_payload_digest(event: CanonicalWorkflowEvent) -> str:
    return workflow_transition_effect_resource_digest({"payload": event.payload})


def _event_content_digest(event: CanonicalWorkflowEvent) -> str:
    return workflow_transition_effect_resource_digest({"event": {**event.to_dict(), "sequence": 0}})


def _event_mapping(event: CanonicalWorkflowEvent | None) -> dict[str, Any] | None:
    return event.to_dict() if event is not None else None


def _commit_mapping(commit: WorkflowEventCommitProof | None) -> dict[str, Any] | None:
    return commit.to_dict() if commit is not None else None


def _bounded_event(event: Mapping[str, Any]) -> None:
    try:
        size = len(canonical_json(event).encode("utf-8"))
    except (OverflowError, TypeError, ValueError, UnicodeEncodeError) as exc:
        raise WorkflowTransitionEventEffectError("workflow_transition_event_payload_invalid") from exc
    if size > _MAX_EVENT_BYTES:
        raise WorkflowTransitionEventEffectError("workflow_transition_event_payload_too_large")


def _json_mapping(
    value: Any,
    *,
    reason: str,
    empty: bool,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise WorkflowTransitionEventEffectError(f"workflow_transition_{reason}_invalid")
    try:
        copied = workflow_transition_event_payload_copy(value)
        if not empty and not copied:
            raise TypeError
    except Exception as exc:
        raise WorkflowTransitionEventEffectError(f"workflow_transition_{reason}_invalid") from exc
    return copied


def _identity(value: Any, *, reason: str) -> str:
    return _SCALARS.identity(value, reason)


def _text(value: Any, *, reason: str) -> str:
    return _SCALARS.text(value, reason, maximum=_MAX_TEXT_CHARS)


def _optional_text(value: Any, *, reason: str) -> str:
    if not isinstance(value, str) or value != value.strip() or len(value) > _MAX_TEXT_CHARS or "\x00" in value:
        raise WorkflowTransitionEventEffectError(f"workflow_transition_event_{reason}_invalid")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise WorkflowTransitionEventEffectError(f"workflow_transition_event_{reason}_invalid") from exc
    return value


def _positive_integer(value: Any, *, reason: str) -> int:
    return _SCALARS.positive_integer(value, reason, maximum=_MAX_SEQUENCE)


def _nonnegative_integer(value: Any, *, reason: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _MAX_SEQUENCE:
        raise WorkflowTransitionEventEffectError(f"workflow_transition_event_{reason}_invalid")
    return value


def _positive_timestamp(value: Any, *, reason: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) <= 0
    ):
        raise WorkflowTransitionEventEffectError(f"workflow_transition_event_{reason}_invalid")
    return float(value)


def _sha256(value: Any, *, reason: str) -> str:
    return _SCALARS.sha256(value, reason)


def _opaque_identifier(prefix: str, namespace: str, *parts: str) -> str:
    framed = canonical_json(
        {
            "namespace": namespace,
            "parts": list(parts),
        }
    ).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(framed).hexdigest()}"


__all__ = [
    "WORKFLOW_TRANSITION_EVENT_ACTOR",
    "WORKFLOW_TRANSITION_EVENT_EFFECT_SCHEMA",
    "WORKFLOW_TRANSITION_EVENT_OBSERVATION_SCHEMA",
    "WORKFLOW_TRANSITION_EVENT_RESOURCE_KIND",
    "WORKFLOW_TRANSITION_EVENT_RESULT_SCHEMA",
    "WORKFLOW_TRANSITION_EVENT_SLOT_KIND",
    "WorkflowTransitionEventAuthority",
    "WorkflowTransitionEventEffectError",
    "WorkflowTransitionEventEffectExecutor",
    "WorkflowTransitionEventEffectObserver",
    "assert_active_workflow_transition_event_proof",
    "assert_durable_workflow_transition_event_proof",
    "build_workflow_transition_event_effect",
    "workflow_transition_event_effect_idempotency_key",
    "workflow_transition_event_id",
    "workflow_transition_event_observation_digest",
]
