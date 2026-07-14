"""Composition helpers for optional workflow OTLP export."""

from __future__ import annotations

import json
import os
from typing import Any

from ananta_contracts.file_credentials import (
    FileCredentialConfigurationError,
    read_file_managed_bytes,
)

from agent.services.workflow_runtime.events import CanonicalWorkflowEvent, EventStore
from agent.services.workflow_runtime.telemetry import (
    CanonicalEventTelemetryProjector,
    OpenTelemetryWorkflowExporter,
    TelemetryEventStore,
    WorkflowTelemetryError,
    WorkflowTraceContext,
)


def configure_workflow_telemetry(inner: EventStore) -> EventStore:
    enabled = str(os.environ.get("ANANTA_WORKFLOW_OTEL_ENABLED") or "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return inner
    endpoint = str(os.environ.get("ANANTA_WORKFLOW_OTEL_ENDPOINT") or "").strip()
    if not endpoint:
        raise WorkflowTelemetryError("opentelemetry_endpoint_required")
    exporter = OpenTelemetryWorkflowExporter.from_otlp_http(
        endpoint=endpoint,
        headers=_read_headers_file(),
    )
    return TelemetryEventStore(
        inner,
        CanonicalEventTelemetryProjector(exporter),
        trace_context_factory=_trace_context,
        on_export_error=_audit_export_error,
    )


def _trace_context(event: CanonicalWorkflowEvent) -> WorkflowTraceContext:
    payload = dict(event.payload)
    return WorkflowTraceContext(
        tenant_id=event.tenant_id,
        workflow_id=event.workflow_id,
        run_id=event.run_id,
        trace_id=event.correlation_id,
        node_id=event.step_id,
        hub_task_id=str(payload.get("hub_task_id") or ""),
        worker_id=str(payload.get("worker_id") or ""),
        provider_id=str(payload.get("provider_id") or ""),
        tool_id=str(payload.get("tool_id") or ""),
        activity_id=str(payload.get("activity_id") or ""),
        artifact_id=str(payload.get("artifact_id") or ""),
    )


def _read_headers_file() -> dict[str, str]:
    raw_path = str(os.environ.get("ANANTA_WORKFLOW_OTEL_HEADERS_FILE") or "").strip()
    if not raw_path:
        return {}
    if not os.path.isabs(raw_path):
        raise WorkflowTelemetryError("opentelemetry_headers_file_must_be_absolute")
    try:
        raw = read_file_managed_bytes(
            raw_path,
            description="OpenTelemetry headers file",
            max_bytes=32_768,
        )
    except FileCredentialConfigurationError as exc:
        # Keep the pre-existing public reason categories stable while the shared
        # reader enforces no-follow, single-link, trusted-owner, safe-mode,
        # bounded-read and mutation checks without exposing the configured path.
        reason_code = (
            "opentelemetry_headers_file_unreadable"
            if isinstance(exc.__cause__, (OSError, ValueError))
            else "opentelemetry_headers_file_invalid"
        )
        raise WorkflowTelemetryError(reason_code) from exc
    try:
        decoded: Any = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise WorkflowTelemetryError("opentelemetry_headers_file_invalid") from exc
    if not isinstance(decoded, dict) or any(
        not str(key).strip() or not isinstance(value, str) for key, value in decoded.items()
    ):
        raise WorkflowTelemetryError("opentelemetry_headers_file_invalid")
    return {str(key): value for key, value in decoded.items()}


def _audit_export_error(exception_type: str) -> None:
    from agent.common.audit import log_audit

    log_audit(
        "workflow_telemetry_export_failed",
        {"exception_type": str(exception_type)[:128]},
    )


__all__ = ["configure_workflow_telemetry"]
