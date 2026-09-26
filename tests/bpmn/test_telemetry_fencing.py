"""Telemetry cannot bypass a fenced store or export a rejected stale append."""

from types import SimpleNamespace

import pytest

from agent.services.workflow_runtime import CanonicalWorkflowEvent, InMemoryEventStore
from agent.services.workflow_runtime.errors import OptimisticConcurrencyError
from agent.services.workflow_runtime.telemetry import TelemetryEventStore
from tests.bpmn.test_run_lease import setup


def observed_event(request):
    return CanonicalWorkflowEvent.build(
        tenant_id=request.plan.tenant_id,
        workflow_id=request.plan.workflow_id,
        run_id=request.run_id,
        event_type="workflow.run.started",
        correlation_id=request.run_id,
        causation_id="control",
        dedupe_key="start",
    )


def test_telemetry_decorator_keeps_atomic_fence_and_exports_only_committed_event():
    now, leases, request = setup()
    exports = []
    inner = InMemoryEventStore()
    store = TelemetryEventStore(
        inner,
        SimpleNamespace(export=lambda event, context: exports.append(event)),
        trace_context_factory=lambda event: None,
    )
    with leases.acquire(request) as lease:
        stored = store.append_fenced(observed_event(request), expected_sequence=0, lease=lease)
        assert exports == [stored]
        now[0] += 31
        with pytest.raises(OptimisticConcurrencyError, match="lease_stale"):
            store.append_fenced(observed_event(request), expected_sequence=1, lease=lease)
    assert exports == [stored]
    assert store.list_events(tenant_id=request.plan.tenant_id, run_id=request.run_id) == (stored,)


def test_telemetry_missing_fenced_recipient_does_not_fall_back():
    store = TelemetryEventStore(
        SimpleNamespace(append=lambda *a, **k: pytest.fail("unfenced fallback")),
        None,
        trace_context_factory=lambda event: None,
    )
    with pytest.raises(RuntimeError, match="recipient_fencing_unavailable"):
        store.append_fenced(None, expected_sequence=0, lease=object())


def test_telemetry_failure_cannot_undo_fenced_commit():
    _, leases, request = setup()

    def failed_export(event, context):
        raise RuntimeError("synthetic_exporter_failure")

    store = TelemetryEventStore(
        InMemoryEventStore(), SimpleNamespace(export=failed_export), trace_context_factory=lambda event: None
    )
    with leases.acquire(request) as lease:
        stored = store.append_fenced(observed_event(request), expected_sequence=0, lease=lease)
    assert store.list_events(tenant_id=request.plan.tenant_id, run_id=request.run_id) == (stored,)
