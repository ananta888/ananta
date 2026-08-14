"""Canonical workflow events, event-store port, and rebuildable projections."""

from __future__ import annotations

import hashlib
import math
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from agent.services.identity_validation import (
    IdentityValidationError,
    require_canonical_identity,
)
from agent.services.workflow_runtime._serialization import canonical_json, redact_json, sha256_json
from agent.services.workflow_runtime.errors import (
    ContractIssue,
    ContractValidationError,
    OptimisticConcurrencyError,
)

CANONICAL_WORKFLOW_EVENT_SCHEMA = "ananta.workflow_event.v1"
WORKFLOW_EVENT_TOPIC = "workflow.runtime.events"
WORKFLOW_EVENT_COMMIT_INLINE = "inline_atomic"
WORKFLOW_EVENT_COMMIT_OUTBOX = "transactional_outbox"
WORKFLOW_EVENT_COMMIT_MODES = frozenset(
    {
        WORKFLOW_EVENT_COMMIT_INLINE,
        WORKFLOW_EVENT_COMMIT_OUTBOX,
    }
)
_CANONICAL_EVENT_FIELDS = frozenset(
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
_MAX_TRANSITION_EVENT_BYTES = 262_144
_MAX_TRANSITION_JSON_DEPTH = 32
_MAX_TRANSITION_JSON_ITEMS = 10_000
_EVENT_TYPE_UNSET = object()


@dataclass(slots=True)
class _TransitionJsonBudget:
    remaining_items: int = _MAX_TRANSITION_JSON_ITEMS
    remaining_bytes: int = _MAX_TRANSITION_EVENT_BYTES

    def consume(self, value: str = "") -> None:
        self.remaining_items -= 1
        if self.remaining_items < 0:
            raise OptimisticConcurrencyError("workflow_transition_event_payload_invalid")
        try:
            self.remaining_bytes -= len(value.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise OptimisticConcurrencyError("workflow_transition_event_payload_invalid") from exc
        if self.remaining_bytes < 0:
            raise OptimisticConcurrencyError("workflow_transition_event_payload_too_large")


@dataclass(frozen=True)
class CanonicalWorkflowEvent:
    tenant_id: str
    workflow_id: str
    run_id: str
    event_type: str
    correlation_id: str
    causation_id: str
    dedupe_key: str
    sequence: int = 0
    step_id: str = ""
    attempt: int = 0
    actor: str = "system"
    occurred_at: float = field(default_factory=time.time)
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: f"wfe-{uuid.uuid4().hex}")
    schema: str = CANONICAL_WORKFLOW_EVENT_SCHEMA

    @classmethod
    def build(
        cls,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        event_type: str,
        correlation_id: str,
        causation_id: str,
        dedupe_key: str = "",
        step_id: str = "",
        attempt: int = 0,
        actor: str = "system",
        payload: dict[str, Any] | None = None,
        occurred_at: float | None = None,
        event_id: str | None = None,
    ) -> "CanonicalWorkflowEvent":
        resolved_id = str(event_id or f"wfe-{uuid.uuid4().hex}")
        event = cls(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            run_id=run_id,
            event_type=str(event_type).strip(),
            correlation_id=str(correlation_id).strip(),
            causation_id=str(causation_id).strip(),
            dedupe_key=str(dedupe_key or resolved_id).strip(),
            step_id=str(step_id).strip(),
            attempt=int(attempt),
            actor=str(actor or "system").strip() or "system",
            occurred_at=float(occurred_at if occurred_at is not None else time.time()),
            payload=dict(redact_json(dict(payload or {}))),
            event_id=resolved_id,
        )
        event.assert_valid(allow_unsequenced=True)
        return event

    @classmethod
    def from_mapping(cls, raw: dict[str, Any], *, validate: bool = True) -> "CanonicalWorkflowEvent":
        from agent.services.workflow_runtime.compatibility import (
            upcast_runtime_contract_for_loading,
        )

        raw = upcast_runtime_contract_for_loading(raw, contract_type="event")
        event = cls(
            tenant_id=raw.get("tenant_id"),
            workflow_id=raw.get("workflow_id"),
            run_id=raw.get("run_id"),
            event_type=str(raw.get("event_type") or "").strip(),
            correlation_id=str(raw.get("correlation_id") or "").strip(),
            causation_id=str(raw.get("causation_id") or "").strip(),
            dedupe_key=str(raw.get("dedupe_key") or raw.get("event_id") or "").strip(),
            sequence=int(raw.get("sequence") or 0),
            step_id=str(raw.get("step_id") or "").strip(),
            attempt=int(raw.get("attempt") or 0),
            actor=str(raw.get("actor") or "system").strip() or "system",
            occurred_at=float(raw.get("occurred_at") or raw.get("timestamp") or time.time()),
            payload=dict(redact_json(dict(raw.get("payload") or {}))),
            event_id=str(raw.get("event_id") or "").strip(),
            schema=str(raw.get("schema") or CANONICAL_WORKFLOW_EVENT_SCHEMA),
        )
        if validate:
            event.assert_valid(allow_unsequenced=False)
        return event

    def validate(self, *, allow_unsequenced: bool = False) -> tuple[ContractIssue, ...]:
        issues: list[ContractIssue] = []
        for name, value in (
            ("tenant_id", self.tenant_id),
            ("workflow_id", self.workflow_id),
            ("run_id", self.run_id),
        ):
            try:
                require_canonical_identity(value, field_name=name)
            except IdentityValidationError as exc:
                issues.append(ContractIssue(exc.reason_code, exc.field_name))
        for name, value in (
            ("event_type", self.event_type),
            ("correlation_id", self.correlation_id),
            ("causation_id", self.causation_id),
            ("dedupe_key", self.dedupe_key),
            ("event_id", self.event_id),
        ):
            if not value:
                issues.append(ContractIssue(f"{name}_required", name))
        if self.schema != CANONICAL_WORKFLOW_EVENT_SCHEMA:
            issues.append(ContractIssue("workflow_event_schema_unsupported", "schema"))
        if self.sequence < (0 if allow_unsequenced else 1):
            issues.append(ContractIssue("sequence_invalid", "sequence"))
        if self.attempt < 0:
            issues.append(ContractIssue("attempt_invalid", "attempt"))
        if self.occurred_at <= 0:
            issues.append(ContractIssue("occurred_at_invalid", "occurred_at"))
        return tuple(issues)

    def assert_valid(self, *, allow_unsequenced: bool = False) -> None:
        issues = self.validate(allow_unsequenced=allow_unsequenced)
        if issues:
            raise ContractValidationError(*issues)

    def with_sequence(self, sequence: int) -> "CanonicalWorkflowEvent":
        result = replace(self, sequence=int(sequence))
        result.assert_valid()
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "event_id": self.event_id,
            "tenant_id": self.tenant_id,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "step_id": self.step_id,
            "attempt": self.attempt,
            "event_type": self.event_type,
            "actor": self.actor,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "sequence": self.sequence,
            "dedupe_key": self.dedupe_key,
            "occurred_at": self.occurred_at,
            "payload": dict(self.payload),
        }

    @property
    def content_hash(self) -> str:
        payload = self.to_dict()
        payload.pop("sequence", None)
        # ``dedupe_key`` is the semantic identity. Transport retries may assign a
        # fresh event ID while carrying the same canonical event content.
        payload.pop("event_id", None)
        return sha256_json(payload)


class EventStore(Protocol):
    """Append-only, tenant-bound canonical event storage."""

    def append(self, event: CanonicalWorkflowEvent, *, expected_sequence: int) -> CanonicalWorkflowEvent: ...

    def list_events(
        self,
        *,
        tenant_id: str,
        run_id: str,
        after_sequence: int = 0,
        limit: int | None = None,
    ) -> tuple[CanonicalWorkflowEvent, ...]: ...


class WorkflowTransitionEventAppendPort(Protocol):
    """Append one transition event through the raw authoritative store."""

    def append_transition_event(
        self,
        event: CanonicalWorkflowEvent,
        *,
        expected_sequence: int,
    ) -> CanonicalWorkflowEvent: ...


@dataclass(frozen=True, slots=True)
class WorkflowEventCommitProof:
    """Immutable backend commit projection; mutable publisher state is excluded."""

    commit_id: str
    delivery_mode: str
    tenant_id: str
    aggregate_id: str
    topic: str
    dedupe_key: str
    created_at: float
    payload_digest: str

    def __post_init__(self) -> None:
        tenant = require_canonical_identity(self.tenant_id, field_name="tenant_id")
        run = require_canonical_identity(self.aggregate_id, field_name="run_id")
        if self.delivery_mode not in WORKFLOW_EVENT_COMMIT_MODES:
            raise OptimisticConcurrencyError("workflow_event_commit_mode_invalid")
        if self.topic != WORKFLOW_EVENT_TOPIC:
            raise OptimisticConcurrencyError("workflow_event_commit_topic_conflict")
        _transition_event_identifier(
            self.commit_id,
            maximum=80,
            reason="commit_id",
        )
        _transition_event_identifier(
            self.dedupe_key,
            maximum=768,
            reason="outbox_dedupe_key",
        )
        for value in (self.created_at,):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise OptimisticConcurrencyError("workflow_event_commit_timestamp_invalid")
            if float(value) <= 0:
                raise OptimisticConcurrencyError("workflow_event_commit_timestamp_invalid")
        if (
            not isinstance(self.payload_digest, str)
            or len(self.payload_digest) != 64
            or any(character not in "0123456789abcdef" for character in self.payload_digest)
        ):
            raise OptimisticConcurrencyError("workflow_event_commit_payload_digest_invalid")
        if tenant != self.tenant_id or run != self.aggregate_id:
            raise OptimisticConcurrencyError("workflow_event_commit_binding_conflict")

    @classmethod
    def for_event(
        cls,
        event: CanonicalWorkflowEvent,
        *,
        delivery_mode: str = WORKFLOW_EVENT_COMMIT_INLINE,
    ) -> WorkflowEventCommitProof:
        return cls(
            commit_id=workflow_event_outbox_id(event),
            delivery_mode=delivery_mode,
            tenant_id=event.tenant_id,
            aggregate_id=event.run_id,
            topic=WORKFLOW_EVENT_TOPIC,
            dedupe_key=workflow_event_delivery_dedupe_key(event),
            created_at=event.occurred_at,
            payload_digest=sha256_json(event.to_dict()),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "commit_id": self.commit_id,
            "delivery_mode": self.delivery_mode,
            "tenant_id": self.tenant_id,
            "aggregate_id": self.aggregate_id,
            "topic": self.topic,
            "dedupe_key": self.dedupe_key,
            "created_at": self.created_at,
            "payload_digest": self.payload_digest,
        }


@dataclass(frozen=True, slots=True)
class WorkflowEventIdentityHeadSnapshot:
    """One atomic read of both event identities, stream head, and commit proof."""

    tenant_id: str
    workflow_id: str
    run_id: str
    dedupe_key: str
    event_id: str
    delivery_mode: str
    dedupe_event: CanonicalWorkflowEvent | None
    event_id_event: CanonicalWorkflowEvent | None
    head_event: CanonicalWorkflowEvent | None
    dedupe_commit: WorkflowEventCommitProof | None
    event_id_commit: WorkflowEventCommitProof | None

    def __post_init__(self) -> None:
        expected = workflow_transition_event_observation_binding(
            tenant_id=self.tenant_id,
            workflow_id=self.workflow_id,
            run_id=self.run_id,
            dedupe_key=self.dedupe_key,
            event_id=self.event_id,
        )
        if self.delivery_mode not in WORKFLOW_EVENT_COMMIT_MODES:
            raise OptimisticConcurrencyError("workflow_event_observation_mode_invalid")
        cloned_events: list[CanonicalWorkflowEvent | None] = []
        for role, event in (
            ("dedupe", self.dedupe_event),
            ("event_id", self.event_id_event),
            ("head", self.head_event),
        ):
            if event is None:
                cloned_events.append(None)
                continue
            if not isinstance(event, CanonicalWorkflowEvent):
                raise OptimisticConcurrencyError("workflow_event_observation_invalid")
            cloned = canonical_workflow_event_from_exact_mapping(event.to_dict())
            if (cloned.tenant_id, cloned.workflow_id, cloned.run_id) != expected[:3]:
                raise OptimisticConcurrencyError("workflow_event_observation_binding_conflict")
            if role == "dedupe" and cloned.dedupe_key != expected[3]:
                raise OptimisticConcurrencyError("workflow_event_observation_binding_conflict")
            if role == "event_id" and cloned.event_id != expected[4]:
                raise OptimisticConcurrencyError("workflow_event_observation_binding_conflict")
            cloned_events.append(cloned)
        dedupe_event, event_id_event, head_event = cloned_events
        if head_event is None and (dedupe_event is not None or event_id_event is not None):
            raise OptimisticConcurrencyError("workflow_event_observation_head_conflict")
        if head_event is not None:
            for event in (dedupe_event, event_id_event):
                if event is not None and event.sequence > head_event.sequence:
                    raise OptimisticConcurrencyError("workflow_event_observation_head_conflict")
        for commit in (self.dedupe_commit, self.event_id_commit):
            if commit is not None and not isinstance(commit, WorkflowEventCommitProof):
                raise OptimisticConcurrencyError("workflow_event_commit_invalid")
            if commit is not None and (
                commit.tenant_id != expected[0]
                or commit.aggregate_id != expected[2]
                or commit.delivery_mode != self.delivery_mode
            ):
                raise OptimisticConcurrencyError("workflow_event_commit_binding_conflict")
        object.__setattr__(self, "dedupe_event", dedupe_event)
        object.__setattr__(self, "event_id_event", event_id_event)
        object.__setattr__(self, "head_event", head_event)

    @property
    def head_sequence(self) -> int:
        return self.head_event.sequence if self.head_event is not None else 0


class WorkflowTransitionEventObservationReadPort(Protocol):
    """Atomically read transition event identities, head, and commit evidence."""

    def observe_transition_event(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        dedupe_key: str,
        event_id: str,
    ) -> WorkflowEventIdentityHeadSnapshot: ...


class WorkflowEventDedupeReadPort(Protocol):
    """Read one exact transition-owned event identity.

    The bounded canonical key contract is intentionally narrower than legacy
    ``EventStore`` inputs; the existing broad mutation protocol is unchanged.
    """

    def get_by_dedupe(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        dedupe_key: str,
    ) -> CanonicalWorkflowEvent | None: ...


class InMemoryEventStore:
    """Thread-safe reference implementation of :class:`EventStore`."""

    def __init__(self) -> None:
        self._events: dict[tuple[str, str], list[CanonicalWorkflowEvent]] = {}
        self._dedupe: dict[tuple[str, str, str], CanonicalWorkflowEvent] = {}
        self._event_ids: dict[tuple[str, str, str], CanonicalWorkflowEvent] = {}
        self._lock = threading.RLock()

    def append(self, event: CanonicalWorkflowEvent, *, expected_sequence: int) -> CanonicalWorkflowEvent:
        event.assert_valid(allow_unsequenced=True)
        key = (event.tenant_id, event.run_id)
        dedupe_key = (*key, event.dedupe_key)
        with self._lock:
            duplicate = self._dedupe.get(dedupe_key)
            if duplicate is not None:
                if duplicate.content_hash != event.content_hash:
                    raise OptimisticConcurrencyError("dedupe_key_payload_conflict")
                return _clone_event(duplicate)
            identity = self._event_ids.get((*key, event.event_id))
            if identity is not None:
                raise OptimisticConcurrencyError("event_id_payload_conflict")
            current = len(self._events.get(key, ()))
            if int(expected_sequence) != current:
                raise OptimisticConcurrencyError(
                    f"event_sequence_conflict:expected={expected_sequence}:actual={current}"
                )
            stored = _clone_event(event.with_sequence(current + 1))
            self._events.setdefault(key, []).append(stored)
            self._dedupe[dedupe_key] = stored
            self._event_ids[(*key, stored.event_id)] = stored
            return _clone_event(stored)

    def append_transition_event(
        self,
        event: CanonicalWorkflowEvent,
        *,
        expected_sequence: int,
    ) -> CanonicalWorkflowEvent:
        stored = self.append(event, expected_sequence=expected_sequence)
        if not event_payload_equal(stored, event):
            raise OptimisticConcurrencyError("workflow_transition_event_identity_conflict")
        return stored

    def list_events(
        self,
        *,
        tenant_id: str,
        run_id: str,
        after_sequence: int = 0,
        limit: int | None = None,
    ) -> tuple[CanonicalWorkflowEvent, ...]:
        validated_tenant_id = require_canonical_identity(
            tenant_id,
            field_name="tenant_id",
        )
        validated_run_id = require_canonical_identity(
            run_id,
            field_name="run_id",
        )
        with self._lock:
            values = [
                event
                for event in self._events.get((validated_tenant_id, validated_run_id), ())
                if event.sequence > int(after_sequence)
            ]
            if limit is not None:
                values = values[: max(0, int(limit))]
            return tuple(_clone_event(event) for event in values)

    def get_by_dedupe(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        dedupe_key: str,
    ) -> CanonicalWorkflowEvent | None:
        tenant, workflow, run, dedupe = workflow_event_dedupe_read_binding(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            run_id=run_id,
            dedupe_key=dedupe_key,
        )
        with self._lock:
            event = self._dedupe.get((tenant, run, dedupe))
            if event is None:
                return None
            assert_workflow_event_dedupe_read_binding(
                event,
                expected=(tenant, workflow, run, dedupe),
            )
            return canonical_workflow_event_from_exact_mapping(event.to_dict())

    def observe_transition_event(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        dedupe_key: str,
        event_id: str,
    ) -> WorkflowEventIdentityHeadSnapshot:
        tenant, workflow, run, dedupe, identity = workflow_transition_event_observation_binding(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            run_id=run_id,
            dedupe_key=dedupe_key,
            event_id=event_id,
        )
        with self._lock:
            stream = self._events.get((tenant, run), ())
            if any(event.workflow_id != workflow for event in stream):
                raise OptimisticConcurrencyError("workflow_event_observation_binding_conflict")
            dedupe_event = self._dedupe.get((tenant, run, dedupe))
            event_id_event = self._event_ids.get((tenant, run, identity))
            head_event = stream[-1] if stream else None
            return WorkflowEventIdentityHeadSnapshot(
                tenant_id=tenant,
                workflow_id=workflow,
                run_id=run,
                dedupe_key=dedupe,
                event_id=identity,
                delivery_mode=WORKFLOW_EVENT_COMMIT_INLINE,
                dedupe_event=dedupe_event,
                event_id_event=event_id_event,
                head_event=head_event,
                dedupe_commit=(WorkflowEventCommitProof.for_event(dedupe_event) if dedupe_event is not None else None),
                event_id_commit=(
                    WorkflowEventCommitProof.for_event(event_id_event) if event_id_event is not None else None
                ),
            )


@dataclass
class WorkflowRunProjection:
    """Rebuildable operational read model derived only from canonical events."""

    tenant_id: str
    run_id: str
    workflow_id: str = ""
    status: str = "pending"
    sequence: int = 0
    steps: dict[str, dict[str, Any]] = field(default_factory=dict)
    approvals: dict[str, dict[str, Any]] = field(default_factory=dict)
    budgets: dict[str, Any] = field(default_factory=dict)
    operations: dict[str, dict[str, Any]] = field(default_factory=dict)
    _seen_dedupe_keys: set[str] = field(default_factory=set, repr=False)

    def apply(self, event: CanonicalWorkflowEvent) -> bool:
        event.assert_valid()
        if event.tenant_id != self.tenant_id or event.run_id != self.run_id:
            raise ContractValidationError("projection_binding_mismatch")
        if event.dedupe_key in self._seen_dedupe_keys:
            return False
        if event.sequence != self.sequence + 1:
            raise OptimisticConcurrencyError(
                f"projection_sequence_gap:expected={self.sequence + 1}:actual={event.sequence}"
            )
        self.workflow_id = event.workflow_id
        event_type = event.event_type
        if event_type == "workflow.run.started":
            self.status = "running"
        elif event_type in {"workflow.run.completed", "workflow.run.failed", "workflow.run.cancelled"}:
            self.status = event_type.rsplit(".", 1)[-1]
        elif event_type == "workflow.run.status.changed":
            observed_status = str(event.payload.get("status") or "").strip().lower()
            if observed_status:
                self.status = observed_status
        elif event_type.startswith("workflow.step.") and event.step_id:
            step = dict(self.steps.get(event.step_id) or {})
            step.update(
                {
                    "status": event_type.rsplit(".", 1)[-1],
                    "attempt": event.attempt,
                    "sequence": event.sequence,
                    "payload": dict(event.payload),
                }
            )
            self.steps[event.step_id] = step
        elif event_type.startswith("workflow.approval."):
            gate_id = str(event.payload.get("gate_id") or event.step_id)
            if gate_id:
                self.approvals[gate_id] = {
                    "status": event_type.rsplit(".", 1)[-1],
                    "sequence": event.sequence,
                    "actor": event.actor,
                }
        elif event_type == "workflow.budget.updated":
            self.budgets.update(dict(event.payload))
        elif event_type.startswith("workflow.side_effect."):
            operation_id = str(event.payload.get("operation_id") or "")
            if operation_id:
                self.operations[operation_id] = {
                    "status": event_type.rsplit(".", 1)[-1],
                    "sequence": event.sequence,
                }
        self._seen_dedupe_keys.add(event.dedupe_key)
        self.sequence = event.sequence
        return True

    @classmethod
    def rebuild(
        cls,
        *,
        tenant_id: str,
        run_id: str,
        events: tuple[CanonicalWorkflowEvent, ...] | list[CanonicalWorkflowEvent],
    ) -> "WorkflowRunProjection":
        projection = cls(tenant_id=tenant_id, run_id=run_id)
        for event in sorted(events, key=lambda item: item.sequence):
            projection.apply(event)
        return projection

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "ananta.workflow_run_projection.v1",
            "tenant_id": self.tenant_id,
            "run_id": self.run_id,
            "workflow_id": self.workflow_id,
            "status": self.status,
            "sequence": self.sequence,
            "steps": dict(self.steps),
            "approvals": dict(self.approvals),
            "budgets": dict(self.budgets),
            "operations": dict(self.operations),
        }


class LegacyWorkflowBackendEventAdapter:
    """Maps existing ``ananta.workflow_backend_event.v1`` dictionaries."""

    @staticmethod
    def adapt(
        raw: dict[str, Any],
        *,
        tenant_id: str,
        run_id: str,
        correlation_id: str,
        causation_id: str,
    ) -> CanonicalWorkflowEvent:
        details = dict(raw.get("details") or {})
        digest = sha256_json(redact_json(raw))
        legacy_type = str(raw.get("event_type") or "unknown").strip().replace("_", ".")
        event_type = legacy_type if legacy_type.startswith("workflow.") else f"workflow.legacy.{legacy_type}"
        return CanonicalWorkflowEvent.build(
            tenant_id=tenant_id,
            workflow_id=raw.get("workflow_id"),
            run_id=run_id,
            event_type=event_type,
            correlation_id=correlation_id,
            causation_id=causation_id,
            dedupe_key=str(raw.get("event_id") or digest),
            step_id=str(details.get("step_id") or ""),
            attempt=int(details.get("attempt") or 0),
            actor=str(raw.get("actor") or "system"),
            payload={"legacy_status": raw.get("status"), **details},
            occurred_at=float(raw.get("occurred_at") or raw.get("timestamp") or _stable_legacy_timestamp(digest)),
            event_id=str(raw.get("event_id") or f"wfe-legacy-{digest}"),
        )


def event_payload_equal(left: CanonicalWorkflowEvent, right: CanonicalWorkflowEvent) -> bool:
    """Useful for cross-runtime conformance tests without comparing sequence."""

    return canonical_json({**left.to_dict(), "sequence": 0}) == canonical_json({**right.to_dict(), "sequence": 0})


def workflow_transition_event_payload_copy(value: Any) -> dict[str, Any]:
    """Return one bounded detached JSON payload without implicit redaction."""

    try:
        copied = _copy_transition_json(
            value,
            depth=0,
            budget=_TransitionJsonBudget(),
            ancestors=set(),
        )
        if not isinstance(copied, dict):
            raise TypeError
        encoded = canonical_json(copied).encode("utf-8")
    except OptimisticConcurrencyError:
        raise
    except (OverflowError, TypeError, ValueError, RecursionError, UnicodeEncodeError) as exc:
        raise OptimisticConcurrencyError("workflow_transition_event_payload_invalid") from exc
    if len(encoded) > _MAX_TRANSITION_EVENT_BYTES:
        raise OptimisticConcurrencyError("workflow_transition_event_payload_too_large")
    if canonical_json(redact_json(copied)) != canonical_json(copied):
        raise OptimisticConcurrencyError("workflow_transition_event_payload_sensitive")
    return copied


def canonical_workflow_event_from_exact_mapping(
    raw: Mapping[str, Any],
    *,
    allow_unsequenced: bool = False,
) -> CanonicalWorkflowEvent:
    """Hydrate a transition event without defaults, coercion, or upcasting."""

    try:
        if not isinstance(raw, Mapping) or set(raw) != _CANONICAL_EVENT_FIELDS:
            raise TypeError
        if raw["schema"] != CANONICAL_WORKFLOW_EVENT_SCHEMA:
            raise TypeError
        for field_name in ("tenant_id", "workflow_id", "run_id"):
            require_canonical_identity(raw[field_name], field_name=field_name)
        for field_name in (
            "event_id",
            "event_type",
            "actor",
            "correlation_id",
            "causation_id",
            "dedupe_key",
        ):
            _transition_event_identifier(
                raw[field_name],
                maximum=512,
                reason=field_name,
            )
        step_id = raw["step_id"]
        if not isinstance(step_id, str) or step_id != step_id.strip() or len(step_id) > 512 or "\x00" in step_id:
            raise TypeError
        step_id.encode("utf-8")
        sequence = raw["sequence"]
        minimum_sequence = 0 if allow_unsequenced else 1
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < minimum_sequence:
            raise TypeError
        attempt = raw["attempt"]
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 0:
            raise TypeError
        occurred_at = raw["occurred_at"]
        if (
            isinstance(occurred_at, bool)
            or not isinstance(occurred_at, (int, float))
            or not math.isfinite(float(occurred_at))
            or float(occurred_at) <= 0
        ):
            raise TypeError
        payload = workflow_transition_event_payload_copy(raw["payload"])
        event = CanonicalWorkflowEvent(
            tenant_id=raw["tenant_id"],
            workflow_id=raw["workflow_id"],
            run_id=raw["run_id"],
            event_type=raw["event_type"],
            correlation_id=raw["correlation_id"],
            causation_id=raw["causation_id"],
            dedupe_key=raw["dedupe_key"],
            sequence=sequence,
            step_id=step_id,
            attempt=attempt,
            actor=raw["actor"],
            occurred_at=float(occurred_at),
            payload=payload,
            event_id=raw["event_id"],
            schema=raw["schema"],
        )
        event.assert_valid(allow_unsequenced=allow_unsequenced)
        if canonical_json(event.to_dict()) != canonical_json(dict(raw)):
            raise TypeError
        if len(canonical_json(event.to_dict()).encode("utf-8")) > _MAX_TRANSITION_EVENT_BYTES:
            raise OptimisticConcurrencyError("workflow_transition_event_payload_too_large")
        return event
    except OptimisticConcurrencyError:
        raise
    except Exception as exc:
        raise OptimisticConcurrencyError("workflow_transition_event_record_invalid") from exc


def workflow_event_dedupe_read_binding(
    *,
    tenant_id: str,
    workflow_id: str,
    run_id: str,
    dedupe_key: str,
) -> tuple[str, str, str, str]:
    tenant = require_canonical_identity(tenant_id, field_name="tenant_id")
    workflow = require_canonical_identity(
        workflow_id,
        field_name="workflow_id",
    )
    run = require_canonical_identity(run_id, field_name="run_id")
    if (
        not isinstance(dedupe_key, str)
        or not dedupe_key
        or dedupe_key != dedupe_key.strip()
        or len(dedupe_key) > 512
        or "\x00" in dedupe_key
    ):
        raise ValueError("workflow_event_dedupe_key_invalid")
    return tenant, workflow, run, dedupe_key


def workflow_transition_event_observation_binding(
    *,
    tenant_id: str,
    workflow_id: str,
    run_id: str,
    dedupe_key: str,
    event_id: str,
) -> tuple[str, str, str, str, str]:
    tenant, workflow, run, dedupe = workflow_event_dedupe_read_binding(
        tenant_id=tenant_id,
        workflow_id=workflow_id,
        run_id=run_id,
        dedupe_key=dedupe_key,
    )
    identity = _transition_event_identifier(
        event_id,
        maximum=512,
        reason="event_id",
    )
    return tenant, workflow, run, dedupe, identity


def assert_workflow_transition_event_record_projection(
    event: CanonicalWorkflowEvent,
    *,
    tenant_id: Any,
    workflow_id: Any,
    run_id: Any,
    sequence: Any,
    dedupe_key: Any,
    event_id: Any,
    content_hash: Any,
    occurred_at: Any,
    event_type: Any = _EVENT_TYPE_UNSET,
) -> None:
    """Bind strict canonical JSON to every duplicated immutable row field."""

    if not isinstance(event, CanonicalWorkflowEvent):
        raise OptimisticConcurrencyError("workflow_transition_event_record_projection_conflict")
    actual = (
        tenant_id,
        workflow_id,
        run_id,
        sequence,
        dedupe_key,
        event_id,
        content_hash,
        occurred_at,
    )
    expected = (
        event.tenant_id,
        event.workflow_id,
        event.run_id,
        event.sequence,
        event.dedupe_key,
        event.event_id,
        event.content_hash,
        event.occurred_at,
    )
    if actual != expected or (event_type is not _EVENT_TYPE_UNSET and event_type != event.event_type):
        raise OptimisticConcurrencyError("workflow_transition_event_record_projection_conflict")


def workflow_event_delivery_dedupe_key(event: CanonicalWorkflowEvent) -> str:
    event.assert_valid()
    return f"{event.run_id}:{event.dedupe_key}"


def workflow_event_outbox_id(event: CanonicalWorkflowEvent) -> str:
    delivery_key = workflow_event_delivery_dedupe_key(event)
    framed = "\x1f".join(
        (
            "wfro",
            event.tenant_id,
            WORKFLOW_EVENT_TOPIC,
            delivery_key,
        )
    )
    return f"wfro-{hashlib.sha256(framed.encode('utf-8')).hexdigest()}"


def _transition_event_identifier(value: Any, *, maximum: int, reason: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum or "\x00" in value:
        raise ValueError(f"workflow_transition_event_{reason}_invalid")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"workflow_transition_event_{reason}_invalid") from exc
    return value


def _copy_transition_json(
    value: Any,
    *,
    depth: int,
    budget: _TransitionJsonBudget,
    ancestors: set[int],
) -> Any:
    if depth > _MAX_TRANSITION_JSON_DEPTH:
        raise OptimisticConcurrencyError("workflow_transition_event_payload_invalid")
    if isinstance(value, str):
        budget.consume(value)
        return value
    budget.consume()
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise OptimisticConcurrencyError("workflow_transition_event_payload_invalid")
        return value
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in ancestors:
            raise OptimisticConcurrencyError("workflow_transition_event_payload_invalid")
        ancestors.add(identity)
        try:
            copied: dict[str, Any] = {}
            for key, item in value.items():
                if not isinstance(key, str) or not key or len(key) > 256 or "\x00" in key:
                    raise OptimisticConcurrencyError("workflow_transition_event_payload_invalid")
                budget.consume(key)
                copied[key] = _copy_transition_json(
                    item,
                    depth=depth + 1,
                    budget=budget,
                    ancestors=ancestors,
                )
            return copied
        finally:
            ancestors.remove(identity)
    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in ancestors:
            raise OptimisticConcurrencyError("workflow_transition_event_payload_invalid")
        ancestors.add(identity)
        try:
            return [
                _copy_transition_json(
                    item,
                    depth=depth + 1,
                    budget=budget,
                    ancestors=ancestors,
                )
                for item in value
            ]
        finally:
            ancestors.remove(identity)
    raise OptimisticConcurrencyError("workflow_transition_event_payload_invalid")


def assert_workflow_event_dedupe_read_binding(
    event: CanonicalWorkflowEvent,
    *,
    expected: tuple[str, str, str, str],
) -> None:
    actual = (
        event.tenant_id,
        event.workflow_id,
        event.run_id,
        event.dedupe_key,
    )
    if actual != expected:
        raise OptimisticConcurrencyError("workflow_event_dedupe_binding_conflict")


def _clone_event(event: CanonicalWorkflowEvent) -> CanonicalWorkflowEvent:
    return CanonicalWorkflowEvent.from_mapping(event.to_dict())


def _stable_legacy_timestamp(digest: str) -> float:
    # Legacy events occasionally omitted time. A hash-derived timestamp keeps
    # retries byte-identical instead of inventing a new identity on each read.
    return float(1_700_000_000 + (int(digest[:12], 16) % 31_536_000))
