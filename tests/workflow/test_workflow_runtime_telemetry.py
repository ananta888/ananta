from __future__ import annotations

import pytest

from agent.services.workflow_runtime.events import CanonicalWorkflowEvent, InMemoryEventStore
from agent.services.workflow_runtime.telemetry import (
    MAX_TELEMETRY_ATTRIBUTES,
    CanonicalEventTelemetryProjector,
    TelemetryEventStore,
    WorkflowTelemetryError,
    WorkflowTraceContext,
)
from agent.services.workflow_runtime.telemetry_runtime import configure_workflow_telemetry


class _Exporter:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.spans = []

    def export(self, span) -> None:
        if self.fail:
            raise RuntimeError("collector_offline")
        self.spans.append(span)


def _event(*, payload=None) -> CanonicalWorkflowEvent:
    return CanonicalWorkflowEvent.build(
        tenant_id="tenant-1",
        workflow_id="workflow-1",
        run_id="run-1",
        step_id="node-1",
        event_type="workflow.node.started",
        correlation_id="trace-1",
        causation_id="task-1",
        dedupe_key="node-started-1",
        payload=payload or {},
    ).with_sequence(1)


def _context(**overrides) -> WorkflowTraceContext:
    values = {
        "tenant_id": "tenant-1",
        "workflow_id": "workflow-1",
        "run_id": "run-1",
        "trace_id": "trace-1",
        "node_id": "node-1",
        "hub_task_id": "task-1",
        "worker_id": "worker-1",
        "provider_id": "provider-1",
        "tool_id": "tool-1",
        "activity_id": "activity-1",
        "artifact_id": "artifact-1",
    }
    values.update(overrides)
    return WorkflowTraceContext(**values)


def test_telemetry_links_runtime_entities_without_exposing_tenant_or_secrets() -> None:
    exporter = _Exporter()

    span = CanonicalEventTelemetryProjector(exporter).export(
        _event(payload={"api_key": "never-export", "result": "ok"}),
        _context(),
    )

    assert exporter.spans == [span]
    assert span.trace_id == "trace-1"
    assert span.attributes["ananta.hub_task_id"] == "task-1"
    assert span.attributes["ananta.worker_id"] == "worker-1"
    assert span.attributes["ananta.provider_id"] == "provider-1"
    assert span.attributes["ananta.tool_id"] == "tool-1"
    assert span.attributes["ananta.activity_id"] == "activity-1"
    assert span.attributes["ananta.artifact_id"] == "artifact-1"
    assert "tenant-1" not in str(span.to_dict())
    assert "never-export" not in str(span.to_dict())
    assert len(span.attributes) <= MAX_TELEMETRY_ATTRIBUTES


def test_telemetry_fails_closed_on_cross_tenant_context() -> None:
    projector = CanonicalEventTelemetryProjector(_Exporter())

    with pytest.raises(WorkflowTelemetryError, match="binding_mismatch"):
        projector.export(_event(), _context(tenant_id="tenant-2"))


def test_oversized_payload_is_replaced_by_digest_not_truncated_content() -> None:
    span = CanonicalEventTelemetryProjector(_Exporter()).export(
        _event(payload={"content": "sensitive-body" * 10_000}),
        _context(),
    )

    assert span.payload["reason_code"] == "workflow_telemetry_payload_omitted"
    assert "sensitive-body" not in str(span.payload)


def test_exporter_failure_cannot_rollback_canonical_event() -> None:
    errors: list[str] = []
    inner = InMemoryEventStore()
    decorated = TelemetryEventStore(
        inner,
        CanonicalEventTelemetryProjector(_Exporter(fail=True)),
        trace_context_factory=lambda _event: _context(),
        on_export_error=errors.append,
    )
    unsequenced = _event()
    unsequenced = CanonicalWorkflowEvent.from_mapping(
        {**unsequenced.to_dict(), "sequence": 0},
        validate=False,
    )

    stored = decorated.append(unsequenced, expected_sequence=0)

    assert stored.sequence == 1
    assert inner.list_events(tenant_id="tenant-1", run_id="run-1") == (stored,)
    assert errors == ["RuntimeError"]


def test_runtime_telemetry_is_opt_in_and_requires_explicit_endpoint(monkeypatch) -> None:
    inner = InMemoryEventStore()
    monkeypatch.delenv("ANANTA_WORKFLOW_OTEL_ENABLED", raising=False)

    assert configure_workflow_telemetry(inner) is inner

    monkeypatch.setenv("ANANTA_WORKFLOW_OTEL_ENABLED", "true")
    monkeypatch.delenv("ANANTA_WORKFLOW_OTEL_ENDPOINT", raising=False)
    with pytest.raises(WorkflowTelemetryError, match="endpoint_required"):
        configure_workflow_telemetry(inner)


def test_runtime_telemetry_uses_secret_file_headers_and_exports_after_append(
    monkeypatch,
    tmp_path,
) -> None:
    exporter = _Exporter()
    headers_file = tmp_path / "otel-headers.json"
    headers_file.write_text('{"Authorization":"opaque-collector-credential"}', encoding="utf-8")
    headers_file.chmod(0o600)
    captured = {}

    def build_exporter(**values):
        captured.update(values)
        return exporter

    monkeypatch.setenv("ANANTA_WORKFLOW_OTEL_ENABLED", "true")
    monkeypatch.setenv("ANANTA_WORKFLOW_OTEL_ENDPOINT", "https://collector.test/v1/traces")
    monkeypatch.setenv("ANANTA_WORKFLOW_OTEL_HEADERS_FILE", str(headers_file))
    monkeypatch.setattr(
        "agent.services.workflow_runtime.telemetry_runtime.OpenTelemetryWorkflowExporter.from_otlp_http",
        build_exporter,
    )
    configured = configure_workflow_telemetry(InMemoryEventStore())
    event = _event()
    unsequenced = CanonicalWorkflowEvent.from_mapping(
        {**event.to_dict(), "sequence": 0},
        validate=False,
    )

    configured.append(unsequenced, expected_sequence=0)

    assert captured["headers"] == {"Authorization": "opaque-collector-credential"}
    assert len(exporter.spans) == 1
    assert "opaque-collector-credential" not in str(exporter.spans[0].to_dict())
