"""Canonical workflow event value object and its schema, topic, and commit-mode identifiers."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any

from agent.services.identity_validation import IdentityValidationError, require_canonical_identity
from agent.services.workflow_runtime._serialization import redact_json, sha256_json
from agent.services.workflow_runtime.errors import ContractIssue, ContractValidationError

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
_EVENT_TYPE_UNSET = object()


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
        from agent.services.workflow_runtime.compatibility import upcast_runtime_contract_for_loading

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
