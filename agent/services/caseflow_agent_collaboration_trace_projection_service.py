"""Authorized CaseFlow edge/trace projection over Hub-owned workflow history.

The projection is deliberately read-only.  It consumes the workflow binding and
history that already belong to the Hub, does not persist chat or trace data, and
fails closed when a directional edge cannot be correlated from existing facts.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from agent.common.redaction import VisibilityLevel, redact
from agent.services.identity_validation import (
    IdentityValidationError,
    require_canonical_identity,
)
from agent.services.workflow_route_authorization_service import (
    WorkflowRoutePrincipal,
)
from agent.visual_process.edge_catalog_contract import (
    CASEFLOW_EDGE_CATALOG_METADATA_KEY,
    CASEFLOW_EDGE_CATALOG_SCHEMA,
    MAX_CASEFLOW_EDGE_CATALOG_SIZE,
    CanonicalVisualProcessEdge,
    build_caseflow_edge_catalog,
    edge_from_object,
)

CASEFLOW_EDGE_TRACE_QUERY_SCHEMA = "ananta.caseflow_edge_trace_query.v1"
CASEFLOW_EDGE_TRACE_READ_MODEL_SCHEMA = (
    "ananta.caseflow_edge_trace_read_model.v1"
)

MAX_CASEFLOW_EDGE_TRACE_QUERY_BYTES = 4 * 1024
MAX_CASEFLOW_TRACE_EVENTS = 2048
MAX_CASEFLOW_EDGE_MESSAGES = 64
MAX_CASEFLOW_EDGE_TELEMETRY = 128
MAX_CASEFLOW_EDGE_REFERENCES = 128
MAX_CASEFLOW_MESSAGE_CHARS = 2048
MAX_CASEFLOW_IDENTIFIER_CHARS = 160
MAX_CASEFLOW_REFERENCE_CHARS = 256

_TOKEN_USAGE_KEYS = frozenset(
    {
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "prompt_tokens",
        "completion_tokens",
        "cached_tokens",
        "reasoning_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    }
)

_ACTIVE_EVENT_SUFFIXES = (
    ".delegated",
    ".started",
    ".message",
    ".message.sent",
    ".requested",
)
_TERMINAL_EVENT_SUFFIXES = (
    ".cancelled",
    ".canceled",
    ".completed",
    ".failed",
    ".rejected",
    ".skipped",
    ".succeeded",
)
_ACTIVE_STATUSES = frozenset({"active", "delegated", "running", "started"})
_TERMINAL_STATUSES = frozenset(
    {"cancelled", "canceled", "completed", "failed", "rejected", "skipped", "succeeded"}
)


class CaseflowEdgeTraceProjectionError(ValueError):
    """Stable domain error translated by the additive HTTP endpoint."""

    def __init__(self, reason_code: str, *, status_code: int) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class CaseflowEdgeTraceQuery:
    run_id: str
    schema: str = CASEFLOW_EDGE_TRACE_QUERY_SCHEMA

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "CaseflowEdgeTraceQuery":
        if set(raw) - {"schema", "run_id"}:
            raise CaseflowEdgeTraceProjectionError(
                "caseflow_edge_trace_query_unknown_field",
                status_code=422,
            )
        if raw.get("schema") != CASEFLOW_EDGE_TRACE_QUERY_SCHEMA:
            raise CaseflowEdgeTraceProjectionError(
                "caseflow_edge_trace_query_schema_unsupported",
                status_code=422,
            )
        try:
            run_id = require_canonical_identity(
                raw.get("run_id"),
                field_name="run_id",
            )
        except IdentityValidationError as exc:
            raise CaseflowEdgeTraceProjectionError(
                "caseflow_edge_trace_run_id_invalid",
                status_code=422,
            ) from exc
        return cls(run_id=run_id)


@dataclass(frozen=True)
class _TraceEvent:
    event_type: str
    step_id: str
    event_id: str
    trace_ref: str
    agent_run_ref: str
    correlation_ref: str
    causation_ref: str
    edge_id: str
    source_step_id: str
    target_step_id: str
    status: str
    sequence: int | None
    occurred_at: float | None
    payload: dict[str, Any]
    content_order_key: str

    @property
    def order_key(self) -> tuple[Any, ...]:
        if self.sequence is not None:
            return (
                0,
                self.sequence,
                self.occurred_at or 0.0,
                self.event_id,
                self.content_order_key,
            )
        return (
            1,
            self.occurred_at or 0.0,
            self.event_id,
            self.content_order_key,
        )

    @property
    def activity(self) -> str:
        event_type = self.event_type.lower()
        if self.status in _TERMINAL_STATUSES or event_type.endswith(_TERMINAL_EVENT_SUFFIXES):
            return "inactive"
        if self.status in _ACTIVE_STATUSES or event_type.endswith(_ACTIVE_EVENT_SUFFIXES):
            return "active"
        return "unknown"


class WorkflowBindingReadPort(Protocol):
    def get(self, workflow_id: str) -> Any | None: ...


class WorkflowHistoryReadPort(Protocol):
    def list_workflow_events(self, workflow_id: str) -> Sequence[Mapping[str, Any]]: ...


class CaseflowAgentCollaborationTraceProjectionService:
    """Build a deterministic, tenant-bound projection from existing Hub facts."""

    def __init__(self, bindings: WorkflowBindingReadPort) -> None:
        self._bindings = bindings

    def read(
        self,
        *,
        principal: WorkflowRoutePrincipal,
        workflow_id: str,
        run_id: str,
        history: WorkflowHistoryReadPort,
    ) -> dict[str, Any]:
        workflow_id = _required_identity(workflow_id, "workflow_id")
        run_id = _required_identity(run_id, "run_id")
        binding = self._bindings.get(workflow_id)
        if not _binding_is_authorized(
            binding,
            principal=principal,
            workflow_id=workflow_id,
            run_id=run_id,
        ):
            raise CaseflowEdgeTraceProjectionError(
                "caseflow_workflow_run_not_found",
                status_code=404,
            )
        try:
            raw_events = history.list_workflow_events(workflow_id)
        except CaseflowEdgeTraceProjectionError:
            raise
        if not isinstance(raw_events, Sequence) or isinstance(raw_events, (str, bytes)):
            raise CaseflowEdgeTraceProjectionError(
                "caseflow_edge_trace_history_invalid",
                status_code=502,
            )
        return self.project(binding=binding, raw_events=raw_events)

    def project(
        self,
        *,
        binding: Any,
        raw_events: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        workflow_id = _required_identity(binding.workflow_id, "workflow_id")
        run_id = _required_identity(binding.run_id, "run_id")
        tenant_id = _required_identity(binding.tenant_id, "tenant_id")
        catalog, catalog_reason = _catalog_from_binding(binding)
        source_event_count = len(raw_events)
        selected_events = _select_bounded_event_window(raw_events)
        normalized, rejected_count = _normalize_events(
            selected_events,
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            run_id=run_id,
        )
        ordered = sorted(normalized, key=lambda item: item.order_key)
        truncated_event_count = max(0, source_event_count - len(selected_events))

        if catalog is None:
            return {
                "schema": CASEFLOW_EDGE_TRACE_READ_MODEL_SCHEMA,
                "workflow_id": workflow_id,
                "run_id": run_id,
                "catalog_verification_status": "unverified",
                "verification_status": "unverified",
                "reason_code": catalog_reason,
                "edges": [],
                "telemetry": _projection_telemetry(
                    source_event_count=source_event_count,
                    processed_event_count=len(ordered),
                    rejected_event_count=rejected_count,
                    truncated_event_count=truncated_event_count,
                    correlated_edge_count=0,
                ),
            }

        projected = _project_edges(catalog, ordered)
        projection_verified = all(
            edge["verification_status"] == "verified" for edge in projected
        )
        return {
            "schema": CASEFLOW_EDGE_TRACE_READ_MODEL_SCHEMA,
            "workflow_id": workflow_id,
            "run_id": run_id,
            "catalog_verification_status": "verified",
            "verification_status": (
                "verified" if projection_verified else "unverified"
            ),
            "reason_code": (
                "" if projection_verified else "caseflow_edge_evidence_incomplete"
            ),
            "edges": projected,
            "telemetry": _projection_telemetry(
                source_event_count=source_event_count,
                processed_event_count=len(ordered),
                rejected_event_count=rejected_count,
                truncated_event_count=truncated_event_count,
                correlated_edge_count=sum(
                    edge["verification_status"] == "verified" for edge in projected
                ),
            ),
        }


def get_caseflow_agent_collaboration_trace_projection_service() -> (
    CaseflowAgentCollaborationTraceProjectionService
):
    """Bind the projection to the active Hub control-plane binding store."""

    from agent.services.workflow_control_composition import (
        get_workflow_backend_control_facade,
    )

    return CaseflowAgentCollaborationTraceProjectionService(
        get_workflow_backend_control_facade().bindings
    )


def _required_identity(value: Any, field_name: str) -> str:
    try:
        return require_canonical_identity(
            value,
            field_name=field_name,
            max_length=MAX_CASEFLOW_IDENTIFIER_CHARS,
        )
    except IdentityValidationError as exc:
        raise ValueError(f"caseflow_{field_name}_invalid") from exc


def _binding_is_authorized(
    binding: Any | None,
    *,
    principal: WorkflowRoutePrincipal,
    workflow_id: str,
    run_id: str,
) -> bool:
    return bool(
        binding is not None
        and binding.workflow_id == workflow_id
        and binding.run_id == run_id
        and binding.tenant_id == principal.tenant_id
        and binding.subject_id == principal.subject
    )


def _catalog_from_binding(
    binding: Any,
) -> tuple[tuple[CanonicalVisualProcessEdge, ...] | None, str]:
    request = getattr(binding, "request", None)
    metadata = getattr(request, "metadata", None)
    raw = metadata.get(CASEFLOW_EDGE_CATALOG_METADATA_KEY) if isinstance(metadata, Mapping) else None
    if not isinstance(raw, Mapping):
        return None, "caseflow_edge_catalog_unavailable"
    if raw.get("schema") != CASEFLOW_EDGE_CATALOG_SCHEMA or raw.get("complete") is not True:
        return None, "caseflow_edge_catalog_unverified"
    values = raw.get("edges")
    if not isinstance(values, list) or len(values) > MAX_CASEFLOW_EDGE_CATALOG_SIZE:
        return None, "caseflow_edge_catalog_unverified"
    try:
        rebuilt = build_caseflow_edge_catalog(values)
    except ValueError:
        return None, "caseflow_edge_catalog_unverified"
    if rebuilt != dict(raw):
        return None, "caseflow_edge_catalog_unverified"
    request_steps = getattr(request, "steps", ())
    step_ids = {
        getattr(step, "step_id", "")
        for step in request_steps
        if getattr(step, "step_id", "")
    }
    dependencies = {
        getattr(step, "step_id", ""): set(getattr(step, "depends_on", ()))
        for step in request_steps
        if getattr(step, "step_id", "")
    }
    edges = tuple(edge_from_object(item) for item in rebuilt["edges"])
    if any(
        edge.source_step_id not in step_ids
        or edge.target_step_id not in step_ids
        or (
            edge.edge_kind == "dependency"
            and edge.source_step_id not in dependencies.get(edge.target_step_id, set())
        )
        for edge in edges
    ):
        return None, "caseflow_edge_catalog_topology_mismatch"
    return edges, ""


_RAW_EVENT_FINGERPRINT_FIELDS = (
    "event_type",
    "event_id",
    "step_id",
    "edge_id",
    "canonical_edge_id",
    "source_step_id",
    "target_step_id",
    "status",
    "sequence",
    "occurred_at",
    "timestamp",
    "trace_ref",
    "trace_id",
    "agent_run_id",
    "correlation_id",
    "causation_id",
)
_RAW_PAYLOAD_FINGERPRINT_FIELDS = (
    "step_id",
    "node_id",
    "edge_id",
    "canonical_edge_id",
    "source_step_id",
    "target_step_id",
    "status",
    "legacy_status",
    "trace_ref",
    "trace_id",
    "trace_bundle_ref",
    "agent_run_id",
    "correlation_id",
    "causation_id",
    "message",
    "messages",
    "duration_ms",
    "latency_ms",
    "model",
    "provider",
    "token_usage",
    "cost_micros",
    "tool",
    "tool_name",
    "error",
    "reason_code",
    "last_error",
)


def _select_bounded_event_window(
    raw_events: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    if len(raw_events) <= MAX_CASEFLOW_TRACE_EVENTS:
        return tuple(raw_events)
    return tuple(
        heapq.nlargest(
            MAX_CASEFLOW_TRACE_EVENTS,
            raw_events,
            key=_raw_event_selection_key,
        )
    )


def _raw_event_selection_key(raw: Any) -> tuple[Any, ...]:
    if not isinstance(raw, Mapping):
        return (-1, 0, 0.0, "", "")
    sequence = _positive_integer(raw.get("sequence"))
    occurred_at = _positive_number(raw.get("occurred_at") or raw.get("timestamp"))
    event_id = _bounded_raw_scalar(raw.get("event_id"))
    payload_value = raw.get("payload")
    if not isinstance(payload_value, Mapping):
        payload_value = raw.get("details")
    payload = payload_value if isinstance(payload_value, Mapping) else {}
    fingerprint = {
        "event": {
            key: _bounded_fingerprint_value(raw.get(key))
            for key in _RAW_EVENT_FINGERPRINT_FIELDS
            if raw.get(key) is not None
        },
        "payload": {
            key: _bounded_fingerprint_value(payload.get(key))
            for key in _RAW_PAYLOAD_FINGERPRINT_FIELDS
            if payload.get(key) is not None
        },
    }
    return (
        1 if sequence is not None else 0,
        sequence or 0,
        occurred_at or 0.0,
        event_id,
        _stable_digest(fingerprint),
    )


def _bounded_fingerprint_value(value: Any, *, depth: int = 0) -> Any:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return {"text": value[:MAX_CASEFLOW_MESSAGE_CHARS], "length": len(value)}
    if depth >= 2:
        return type(value).__name__
    if isinstance(value, Mapping):
        items = sorted(value.items(), key=lambda item: str(item[0]))[:32]
        return {
            str(key)[:128]: _bounded_fingerprint_value(item, depth=depth + 1)
            for key, item in items
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return {
            "items": [
                _bounded_fingerprint_value(item, depth=depth + 1)
                for item in value[:MAX_CASEFLOW_EDGE_MESSAGES]
            ],
            "length": len(value),
        }
    return type(value).__name__


def _bounded_raw_scalar(value: Any) -> str:
    return value[:MAX_CASEFLOW_REFERENCE_CHARS] if isinstance(value, str) else ""


def _normalize_events(
    raw_events: Sequence[Mapping[str, Any]],
    *,
    tenant_id: str,
    workflow_id: str,
    run_id: str,
) -> tuple[list[_TraceEvent], int]:
    result: list[_TraceEvent] = []
    rejected = 0
    for raw in raw_events:
        if not isinstance(raw, Mapping):
            rejected += 1
            continue
        explicit_tenant = raw.get("tenant_id")
        explicit_workflow = raw.get("workflow_id")
        explicit_run = raw.get("run_id")
        if (
            (explicit_tenant not in (None, "") and explicit_tenant != tenant_id)
            or (explicit_workflow not in (None, "") and explicit_workflow != workflow_id)
            or (explicit_run not in (None, "") and explicit_run != run_id)
        ):
            rejected += 1
            continue
        payload_value = raw.get("payload")
        if not isinstance(payload_value, Mapping):
            payload_value = raw.get("details")
        raw_payload = payload_value if isinstance(payload_value, Mapping) else {}
        event_type = _safe_text(raw.get("event_type"), maximum=128)
        if not event_type:
            rejected += 1
            continue
        try:
            step_id = _coalesced_optional_identity(
                raw.get("step_id"),
                raw_payload.get("step_id"),
                raw_payload.get("node_id"),
            )
            edge_id = _coalesced_optional_identity(
                raw.get("edge_id"),
                raw.get("canonical_edge_id"),
                raw_payload.get("edge_id"),
                raw_payload.get("canonical_edge_id"),
            )
            source_step_id = _coalesced_optional_identity(
                raw.get("source_step_id"),
                raw_payload.get("source_step_id"),
            )
            target_step_id = _coalesced_optional_identity(
                raw.get("target_step_id"),
                raw_payload.get("target_step_id"),
            )
        except ValueError:
            rejected += 1
            continue
        payload = _bounded_payload(raw_payload)
        event_id = _optional_reference(raw.get("event_id"))
        trace_ref = _optional_reference(
            raw.get("trace_ref")
            or raw.get("trace_id")
            or raw_payload.get("trace_ref")
            or raw_payload.get("trace_id")
            or raw_payload.get("trace_bundle_ref")
        )
        agent_run_ref = _optional_reference(
            raw.get("agent_run_id") or raw_payload.get("agent_run_id")
        )
        correlation_ref = _optional_reference(
            raw.get("correlation_id") or raw_payload.get("correlation_id")
        )
        causation_ref = _optional_reference(
            raw.get("causation_id") or raw_payload.get("causation_id")
        )
        status = _safe_text(
            raw.get("status")
            or raw_payload.get("status")
            or raw_payload.get("legacy_status"),
            maximum=64,
        ).lower()
        sequence = _positive_integer(raw.get("sequence"))
        occurred_at = _positive_number(raw.get("occurred_at") or raw.get("timestamp"))
        content_order_key = _stable_digest(
            {
                "event_type": event_type,
                "step_id": step_id,
                "event_id": event_id,
                "trace_ref": trace_ref,
                "agent_run_ref": agent_run_ref,
                "correlation_ref": correlation_ref,
                "causation_ref": causation_ref,
                "edge_id": edge_id,
                "source_step_id": source_step_id,
                "target_step_id": target_step_id,
                "status": status,
                "sequence": sequence,
                "occurred_at": occurred_at,
                "payload": payload,
            }
        )
        result.append(
            _TraceEvent(
                event_type=event_type,
                step_id=step_id,
                event_id=event_id,
                trace_ref=trace_ref,
                agent_run_ref=agent_run_ref,
                correlation_ref=correlation_ref,
                causation_ref=causation_ref,
                edge_id=edge_id,
                source_step_id=source_step_id,
                target_step_id=target_step_id,
                status=status,
                sequence=sequence,
                occurred_at=occurred_at,
                payload=payload,
                content_order_key=content_order_key,
            )
        )
    return result, rejected


def _project_edges(
    catalog: tuple[CanonicalVisualProcessEdge, ...],
    events: Sequence[_TraceEvent],
) -> list[dict[str, Any]]:
    by_id = {edge.edge_id: edge for edge in catalog}
    by_direction: dict[
        tuple[str, str], list[CanonicalVisualProcessEdge]
    ] = defaultdict(list)
    incoming: dict[str, list[CanonicalVisualProcessEdge]] = defaultdict(list)
    for edge in catalog:
        by_direction[(edge.source_step_id, edge.target_step_id)].append(edge)
        incoming[edge.target_step_id].append(edge)

    correlated: dict[str, list[_TraceEvent]] = defaultdict(list)
    bases: dict[str, str] = {}
    conflicts: set[str] = set()
    for event in events:
        if event.edge_id:
            edge = by_id.get(event.edge_id)
            if edge is None:
                continue
            if (
                event.source_step_id
                and event.source_step_id != edge.source_step_id
            ) or (
                event.target_step_id
                and event.target_step_id != edge.target_step_id
            ):
                conflicts.add(edge.edge_id)
                continue
            correlated[edge.edge_id].append(event)
            bases[edge.edge_id] = "explicit_edge_id"
            continue
        if event.source_step_id and event.target_step_id:
            candidates = by_direction.get(
                (event.source_step_id, event.target_step_id), []
            )
            if len(candidates) == 1:
                edge = candidates[0]
                correlated[edge.edge_id].append(event)
                bases.setdefault(edge.edge_id, "explicit_direction")
            elif len(candidates) > 1:
                conflicts.update(edge.edge_id for edge in candidates)

    for edge in catalog:
        if edge.edge_id in correlated or edge.edge_id in conflicts:
            continue
        inferred = _infer_unique_dependency_events(
            edge,
            incoming=incoming,
            events=events,
        )
        if inferred:
            correlated[edge.edge_id].extend(inferred)
            bases[edge.edge_id] = "unique_dependency_event_sequence"

    for edge in catalog:
        if edge.edge_id not in correlated or edge.edge_id in conflicts:
            continue
        if len(incoming.get(edge.target_step_id, ())) != 1:
            continue
        existing = correlated[edge.edge_id]
        existing_keys = {event.content_order_key for event in existing}
        first_edge_event = min(existing, key=lambda item: item.order_key)
        for event in events:
            if (
                event.content_order_key not in existing_keys
                and not event.edge_id
                and not event.source_step_id
                and not event.target_step_id
                and event.step_id == edge.target_step_id
                and event.activity == "inactive"
                and event.order_key > first_edge_event.order_key
            ):
                existing.append(event)
                existing_keys.add(event.content_order_key)

    return [
        _edge_projection(
            edge,
            events=tuple(correlated.get(edge.edge_id, ())),
            basis=bases.get(edge.edge_id, "unavailable"),
            conflicting=edge.edge_id in conflicts,
        )
        for edge in catalog
    ]


def _infer_unique_dependency_events(
    edge: CanonicalVisualProcessEdge,
    *,
    incoming: Mapping[str, Sequence[CanonicalVisualProcessEdge]],
    events: Sequence[_TraceEvent],
) -> tuple[_TraceEvent, ...]:
    target_incoming = incoming.get(edge.target_step_id, ())
    if len(target_incoming) != 1 or target_incoming[0].edge_id != edge.edge_id:
        return ()
    target_active = [
        event
        for event in events
        if event.step_id == edge.target_step_id and event.activity == "active"
    ]
    if not target_active:
        return ()
    active = target_active[-1]
    source_terminal = [
        event
        for event in events
        if event.step_id == edge.source_step_id
        and event.activity == "inactive"
        and event.order_key < active.order_key
    ]
    if not source_terminal:
        return ()
    evidence = [source_terminal[-1], active]
    target_terminal = [
        event
        for event in events
        if event.step_id == edge.target_step_id
        and event.activity == "inactive"
        and event.order_key > active.order_key
    ]
    if target_terminal:
        evidence.append(target_terminal[-1])
    return tuple(evidence)


def _edge_projection(
    edge: CanonicalVisualProcessEdge,
    *,
    events: tuple[_TraceEvent, ...],
    basis: str,
    conflicting: bool,
) -> dict[str, Any]:
    ordered = tuple(sorted(events, key=lambda item: item.order_key))
    if conflicting:
        activity = "unknown"
        verification = "unverified"
        reason_code = "caseflow_edge_correlation_conflicting"
    elif not ordered:
        activity = "unknown"
        verification = "unverified"
        reason_code = "caseflow_edge_correlation_missing"
    else:
        known_activity = [event.activity for event in ordered if event.activity != "unknown"]
        activity = known_activity[-1] if known_activity else "unknown"
        verification = "verified"
        reason_code = (
            f"caseflow_edge_correlation_verified_{activity}"
            if activity != "unknown"
            else "caseflow_edge_activity_unknown"
        )

    messages = [message for event in ordered for message in _messages(event)]
    telemetry = [_telemetry(event) for event in ordered]
    all_event_refs = _unique_existing(event.event_id for event in ordered)
    all_trace_refs = _unique_existing(event.trace_ref for event in ordered)
    return {
        **edge.to_dict(),
        "activity_status": activity,
        "verification_status": verification,
        "reason_code": reason_code,
        "correlation_basis": basis if verification == "verified" else "unavailable",
        "event_refs": all_event_refs[:MAX_CASEFLOW_EDGE_REFERENCES],
        "trace_refs": all_trace_refs[:MAX_CASEFLOW_EDGE_REFERENCES],
        "messages": messages[:MAX_CASEFLOW_EDGE_MESSAGES],
        "telemetry": telemetry[:MAX_CASEFLOW_EDGE_TELEMETRY],
        "limits": {
            "messages_truncated": max(0, len(messages) - MAX_CASEFLOW_EDGE_MESSAGES),
            "telemetry_truncated": max(0, len(telemetry) - MAX_CASEFLOW_EDGE_TELEMETRY),
            "event_refs_truncated": max(
                0, len(all_event_refs) - MAX_CASEFLOW_EDGE_REFERENCES
            ),
            "trace_refs_truncated": max(
                0, len(all_trace_refs) - MAX_CASEFLOW_EDGE_REFERENCES
            ),
        },
    }


def _messages(event: _TraceEvent) -> list[dict[str, Any]]:
    candidates: list[Any] = []
    if "message" in event.payload:
        candidates.append(event.payload["message"])
    raw_messages = event.payload.get("messages")
    if isinstance(raw_messages, list):
        candidates.extend(raw_messages)
    result: list[dict[str, Any]] = []
    for candidate in candidates:
        role = ""
        source_truncated = False
        if isinstance(candidate, Mapping):
            content = candidate.get("content") or candidate.get("text")
            role = _safe_text(candidate.get("role"), maximum=64)
            source_truncated = candidate.get("_source_truncated") is True
        else:
            content = candidate
        if not isinstance(content, str) or not content:
            continue
        safe_content = _safe_text(content, maximum=MAX_CASEFLOW_MESSAGE_CHARS)
        correlation_ref = next(
            (
                value
                for value in (
                    event.trace_ref,
                    event.correlation_ref,
                    event.causation_ref,
                    event.event_id,
                )
                if value
            ),
            "",
        )
        result.append(
            {
                "content": safe_content,
                "role": role or None,
                "event_ref": event.event_id or None,
                "trace_ref": event.trace_ref or None,
                "correlation_ref": correlation_ref or None,
                "occurred_at": event.occurred_at,
                "verification_status": (
                    "verified" if correlation_ref else "unverified"
                ),
                "truncated": source_truncated or len(content) > len(safe_content),
            }
        )
    return result


def _telemetry(event: _TraceEvent) -> dict[str, Any]:
    payload = event.payload
    return {
        "event_ref": event.event_id or None,
        "trace_ref": event.trace_ref or None,
        "agent_run_ref": event.agent_run_ref or None,
        "correlation_ref": event.correlation_ref or None,
        "causation_ref": event.causation_ref or None,
        "event_type": event.event_type,
        "step_id": event.step_id or None,
        "sequence": event.sequence,
        "occurred_at": event.occurred_at,
        "status": event.status or None,
        "duration_ms": _non_negative_number(
            payload.get("duration_ms") or payload.get("latency_ms")
        ),
        "model": _safe_text(payload.get("model"), maximum=160) or None,
        "provider": _safe_text(payload.get("provider"), maximum=160) or None,
        "token_usage": _bounded_scalar_mapping(payload.get("token_usage")),
        "cost_micros": _non_negative_integer(payload.get("cost_micros")),
        "tool": _safe_text(
            payload.get("tool") or payload.get("tool_name"), maximum=160
        )
        or None,
        "error": _safe_text(
            payload.get("error")
            or payload.get("reason_code")
            or payload.get("last_error"),
            maximum=512,
        )
        or None,
        "redaction_policy": "user",
    }


def _projection_telemetry(
    *,
    source_event_count: int,
    processed_event_count: int,
    rejected_event_count: int,
    truncated_event_count: int,
    correlated_edge_count: int,
) -> dict[str, Any]:
    return {
        "source_event_count": source_event_count,
        "processed_event_count": processed_event_count,
        "rejected_event_count": rejected_event_count,
        "truncated_event_count": truncated_event_count,
        "correlated_edge_count": correlated_edge_count,
        "redaction_policy": "user",
        "messages_per_edge_limit": MAX_CASEFLOW_EDGE_MESSAGES,
        "telemetry_per_edge_limit": MAX_CASEFLOW_EDGE_TELEMETRY,
    }


def _bounded_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if "message" in value:
        message = _bounded_message(value.get("message"))
        if message is not None:
            result["message"] = message
    raw_messages = value.get("messages")
    if isinstance(raw_messages, list):
        messages = [
            message
            for item in raw_messages[:MAX_CASEFLOW_EDGE_MESSAGES]
            if (message := _bounded_message(item)) is not None
        ]
        if messages:
            result["messages"] = messages
    for key, maximum in (
        ("model", 160),
        ("provider", 160),
        ("tool", 160),
        ("tool_name", 160),
        ("error", 512),
        ("reason_code", 512),
        ("last_error", 512),
    ):
        safe = _safe_text(value.get(key), maximum=maximum)
        if safe:
            result[key] = safe
    for key in ("duration_ms", "latency_ms"):
        scalar = _non_negative_number(value.get(key))
        if scalar is not None:
            result[key] = scalar
    cost_micros = _non_negative_integer(value.get("cost_micros"))
    if cost_micros is not None:
        result["cost_micros"] = cost_micros
    token_usage = _bounded_scalar_mapping(value.get("token_usage"))
    if token_usage is not None:
        result["token_usage"] = token_usage
    return result


def _bounded_message(value: Any) -> Any | None:
    if isinstance(value, str):
        return {
            "content": _safe_text(value, maximum=MAX_CASEFLOW_MESSAGE_CHARS),
            "_source_truncated": len(value) > MAX_CASEFLOW_MESSAGE_CHARS,
        }
    if not isinstance(value, Mapping):
        return None
    content = value.get("content") or value.get("text")
    if not isinstance(content, str) or not content:
        return None
    result: dict[str, Any] = {
        "content": _safe_text(content, maximum=MAX_CASEFLOW_MESSAGE_CHARS),
        "_source_truncated": len(content) > MAX_CASEFLOW_MESSAGE_CHARS,
    }
    role = _safe_text(value.get("role"), maximum=64)
    if role:
        result["role"] = role
    return result


def _stable_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_text(value: Any, *, maximum: int) -> str:
    if value is None:
        return ""
    if isinstance(value, Mapping):
        value = value.get("message") or value.get("reason_code") or ""
    if not isinstance(value, str):
        return ""
    safe = redact(value, VisibilityLevel.USER)
    return str(safe)[:maximum]


def _coalesced_optional_identity(*values: Any) -> str:
    normalized: list[str] = []
    for value in values:
        if value is None or value == "":
            continue
        try:
            normalized.append(
                require_canonical_identity(
                    value,
                    field_name="identity",
                    max_length=MAX_CASEFLOW_IDENTIFIER_CHARS,
                )
            )
        except IdentityValidationError as exc:
            raise ValueError("caseflow_event_identity_invalid") from exc
    if len(set(normalized)) > 1:
        raise ValueError("caseflow_event_identity_conflicting")
    return normalized[0] if normalized else ""


def _optional_reference(value: Any) -> str:
    try:
        return require_canonical_identity(
            value,
            field_name="reference",
            required=False,
            max_length=MAX_CASEFLOW_REFERENCE_CHARS,
        )
    except IdentityValidationError:
        return ""


def _positive_integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _non_negative_integer(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _positive_number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result > 0 else None


def _non_negative_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def _bounded_scalar_mapping(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    result: dict[str, Any] = {}
    for raw_key, item in sorted(value.items(), key=lambda entry: str(entry[0]))[:16]:
        key = str(raw_key)
        if key not in _TOKEN_USAGE_KEYS or isinstance(item, bool):
            continue
        if isinstance(item, (int, float)) and math.isfinite(float(item)) and item >= 0:
            result[key] = item
    return result or None


def _unique_existing(values: Any) -> list[str]:
    return sorted({value for value in values if value})


__all__ = [
    "CASEFLOW_EDGE_CATALOG_METADATA_KEY",
    "CASEFLOW_EDGE_CATALOG_SCHEMA",
    "CASEFLOW_EDGE_TRACE_QUERY_SCHEMA",
    "CASEFLOW_EDGE_TRACE_READ_MODEL_SCHEMA",
    "MAX_CASEFLOW_EDGE_CATALOG_SIZE",
    "MAX_CASEFLOW_EDGE_MESSAGES",
    "MAX_CASEFLOW_EDGE_REFERENCES",
    "MAX_CASEFLOW_EDGE_TELEMETRY",
    "MAX_CASEFLOW_EDGE_TRACE_QUERY_BYTES",
    "CaseflowAgentCollaborationTraceProjectionService",
    "CaseflowEdgeTraceProjectionError",
    "CaseflowEdgeTraceQuery",
    "get_caseflow_agent_collaboration_trace_projection_service",
]
