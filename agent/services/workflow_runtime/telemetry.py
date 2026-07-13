"""Bounded OpenTelemetry projection for canonical workflow events.

Telemetry is deliberately downstream of the canonical event store. Exporter
failures never mutate, replace, or acknowledge workflow state.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Protocol
from urllib.parse import urlparse

from agent.services.workflow_runtime._serialization import redact_json
from agent.services.workflow_runtime.events import CanonicalWorkflowEvent, EventStore

WORKFLOW_TELEMETRY_SPAN_SCHEMA = "ananta.workflow_telemetry_span.v1"
MAX_TELEMETRY_PAYLOAD_BYTES = 16 * 1024
MAX_TELEMETRY_ATTRIBUTES = 32


class WorkflowTelemetryError(ValueError):
    pass


@dataclass(frozen=True)
class WorkflowTraceContext:
    tenant_id: str
    workflow_id: str
    run_id: str
    trace_id: str
    node_id: str = ""
    hub_task_id: str = ""
    worker_id: str = ""
    provider_id: str = ""
    tool_id: str = ""
    activity_id: str = ""
    artifact_id: str = ""

    def validate(self) -> None:
        for name in ("tenant_id", "workflow_id", "run_id", "trace_id"):
            value = str(getattr(self, name) or "")
            if not value or len(value) > 256:
                raise WorkflowTelemetryError(f"workflow_telemetry_{name}_invalid")

    def linkage(self) -> dict[str, str]:
        self.validate()
        values = {
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "node_id": self.node_id,
            "hub_task_id": self.hub_task_id,
            "worker_id": self.worker_id,
            "provider_id": self.provider_id,
            "tool_id": self.tool_id,
            "activity_id": self.activity_id,
            "artifact_id": self.artifact_id,
        }
        return {key: value[:256] for key, value in values.items() if value}


@dataclass(frozen=True)
class WorkflowTelemetrySpan:
    name: str
    trace_id: str
    event_id: str
    occurred_at: float
    attributes: dict[str, str | int | float | bool]
    payload: dict[str, Any]
    schema: str = WORKFLOW_TELEMETRY_SPAN_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "name": self.name,
            "trace_id": self.trace_id,
            "event_id": self.event_id,
            "occurred_at": self.occurred_at,
            "attributes": dict(self.attributes),
            "payload": dict(self.payload),
        }


class WorkflowTelemetryExporter(Protocol):
    def export(self, span: WorkflowTelemetrySpan) -> None: ...


class NoopWorkflowTelemetryExporter:
    def export(self, span: WorkflowTelemetrySpan) -> None:
        del span


class OpenTelemetryWorkflowExporter:
    """Optional adapter using only the stable OpenTelemetry API surface."""

    def __init__(self, *, tracer_name: str = "ananta.workflow-runtime") -> None:
        try:
            from opentelemetry import trace
        except ImportError as exc:  # pragma: no cover - optional production extra
            raise WorkflowTelemetryError("opentelemetry_api_unavailable") from exc
        self._tracer = trace.get_tracer(tracer_name)
        self._provider = None

    @classmethod
    def from_otlp_http(
        cls,
        *,
        endpoint: str,
        headers: Mapping[str, str] | None = None,
        service_name: str = "ananta-workflow-runtime",
    ) -> "OpenTelemetryWorkflowExporter":
        parsed = urlparse(str(endpoint))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise WorkflowTelemetryError("opentelemetry_endpoint_invalid")
        if parsed.scheme == "http" and parsed.hostname not in {
            "localhost",
            "127.0.0.1",
            "::1",
            "otel-collector",
        }:
            raise WorkflowTelemetryError("opentelemetry_insecure_remote_endpoint_denied")
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
        except ImportError as exc:  # pragma: no cover - optional production extra
            raise WorkflowTelemetryError("opentelemetry_otlp_extra_unavailable") from exc
        provider = TracerProvider(resource=Resource.create({"service.name": str(service_name)[:128]}))
        exporter = OTLPSpanExporter(
            endpoint=str(endpoint),
            headers={str(key): str(value) for key, value in dict(headers or {}).items()},
        )
        provider.add_span_processor(
            BatchSpanProcessor(
                exporter,
                max_queue_size=2_048,
                max_export_batch_size=256,
                schedule_delay_millis=5_000,
            )
        )
        instance = cls.__new__(cls)
        instance._provider = provider
        instance._tracer = provider.get_tracer("ananta.workflow-runtime")
        return instance

    def export(self, span: WorkflowTelemetrySpan) -> None:
        with self._tracer.start_as_current_span(span.name) as active:
            for key, value in span.attributes.items():
                active.set_attribute(key, value)
            active.set_attribute("ananta.trace_id", span.trace_id)
            active.set_attribute("ananta.event_id", span.event_id)
            active.add_event(
                "ananta.canonical_workflow_event",
                attributes={"ananta.payload": _bounded_payload(span.payload)},
            )

    def shutdown(self) -> None:
        if self._provider is not None:
            self._provider.shutdown()


class CanonicalEventTelemetryProjector:
    def __init__(self, exporter: WorkflowTelemetryExporter) -> None:
        self._exporter = exporter

    def export(
        self,
        event: CanonicalWorkflowEvent,
        context: WorkflowTraceContext,
    ) -> WorkflowTelemetrySpan:
        event.assert_valid()
        context.validate()
        if event.tenant_id != context.tenant_id or event.run_id != context.run_id:
            raise WorkflowTelemetryError("workflow_telemetry_binding_mismatch")
        if event.workflow_id != context.workflow_id:
            raise WorkflowTelemetryError("workflow_telemetry_workflow_binding_mismatch")

        linkage = context.linkage()
        attributes: dict[str, str | int | float | bool] = {
            "ananta.event_type": event.event_type[:128],
            "ananta.event_sequence": event.sequence,
            "ananta.actor": event.actor[:64],
            "ananta.tenant_bucket": _tenant_bucket(context.tenant_id),
            **{f"ananta.{key}": value for key, value in linkage.items()},
        }
        # Attribute keys are fixed and the value count is bounded; arbitrary
        # payload keys never become metric/span labels.
        attributes = dict(list(attributes.items())[:MAX_TELEMETRY_ATTRIBUTES])
        payload = _bounded_redacted_payload(event.payload)
        span = WorkflowTelemetrySpan(
            name=f"ananta.{_event_category(event.event_type)}",
            trace_id=context.trace_id,
            event_id=event.event_id,
            occurred_at=event.occurred_at,
            attributes=attributes,
            payload=payload,
        )
        self._exporter.export(span)
        return span


class TelemetryEventStore:
    """EventStore decorator exporting only after a canonical append succeeds."""

    def __init__(
        self,
        inner: EventStore,
        projector: CanonicalEventTelemetryProjector,
        *,
        trace_context_factory,
        on_export_error=None,
    ) -> None:
        self._inner = inner
        self._projector = projector
        self._trace_context_factory = trace_context_factory
        self._on_export_error = on_export_error

    def append(self, event: CanonicalWorkflowEvent, *, expected_sequence: int) -> CanonicalWorkflowEvent:
        stored = self._inner.append(event, expected_sequence=expected_sequence)
        try:
            context = self._trace_context_factory(stored)
            self._projector.export(stored, context)
        except Exception as exc:  # noqa: BLE001 - exporter isolation boundary
            if self._on_export_error is not None:
                self._on_export_error(type(exc).__name__)
        return stored

    def list_events(
        self,
        *,
        tenant_id: str,
        run_id: str,
        after_sequence: int = 0,
        limit: int | None = None,
    ) -> tuple[CanonicalWorkflowEvent, ...]:
        return self._inner.list_events(
            tenant_id=tenant_id,
            run_id=run_id,
            after_sequence=after_sequence,
            limit=limit,
        )


def _bounded_redacted_payload(raw: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(redact_json(dict(raw)))
    rendered = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    if len(rendered) <= MAX_TELEMETRY_PAYLOAD_BYTES:
        return payload
    return {
        "reason_code": "workflow_telemetry_payload_omitted",
        "payload_digest": hashlib.sha256(rendered).hexdigest(),
        "payload_size_bytes": len(rendered),
    }


def _bounded_payload(payload: Mapping[str, Any]) -> str:
    rendered = json.dumps(
        dict(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return rendered[:MAX_TELEMETRY_PAYLOAD_BYTES]


def _tenant_bucket(tenant_id: str) -> str:
    return hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()[:8]


def _event_category(event_type: str) -> str:
    parts = str(event_type).split(".")
    if len(parts) >= 2 and parts[0] == "workflow":
        return parts[1][:32]
    return "event"


__all__ = [
    "MAX_TELEMETRY_ATTRIBUTES",
    "MAX_TELEMETRY_PAYLOAD_BYTES",
    "WORKFLOW_TELEMETRY_SPAN_SCHEMA",
    "CanonicalEventTelemetryProjector",
    "NoopWorkflowTelemetryExporter",
    "OpenTelemetryWorkflowExporter",
    "TelemetryEventStore",
    "WorkflowTelemetryError",
    "WorkflowTelemetryExporter",
    "WorkflowTelemetrySpan",
    "WorkflowTraceContext",
]
