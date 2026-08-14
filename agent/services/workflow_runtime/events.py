"""Canonical workflow events, event-store port, and rebuildable projections."""

from __future__ import annotations

import threading
import time
import uuid
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
            current = len(self._events.get(key, ()))
            if int(expected_sequence) != current:
                raise OptimisticConcurrencyError(
                    f"event_sequence_conflict:expected={expected_sequence}:actual={current}"
                )
            stored = _clone_event(event.with_sequence(current + 1))
            self._events.setdefault(key, []).append(stored)
            self._dedupe[dedupe_key] = stored
            return _clone_event(stored)

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
            return _clone_event(event)


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
