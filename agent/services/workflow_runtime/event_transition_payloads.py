"""Bounded, exact copying and binding checks for workflow transition event payloads and dedupe reads."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from agent.services.identity_validation import require_canonical_identity
from agent.services.workflow_runtime._serialization import canonical_json, redact_json
from agent.services.workflow_runtime.errors import OptimisticConcurrencyError
from agent.services.workflow_runtime.event_canonical import (
    _CANONICAL_EVENT_FIELDS,
    _EVENT_TYPE_UNSET,
    CANONICAL_WORKFLOW_EVENT_SCHEMA,
    WORKFLOW_EVENT_TOPIC,
    CanonicalWorkflowEvent,
)

_MAX_TRANSITION_EVENT_BYTES = 262_144
_MAX_TRANSITION_JSON_DEPTH = 32
_MAX_TRANSITION_JSON_ITEMS = 10_000


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
