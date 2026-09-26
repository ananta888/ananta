"""Gateway observations share a run stream; graph commands do not become audit."""

from dataclasses import replace

import pytest

from agent.services.native_graph_event_appender import append_gateway_observation, append_native_event
from agent.services.native_graph_orchestration_service import NativeGraphRequest
from agent.services.workflow_runtime import CanonicalWorkflowEvent, InMemoryEventStore
from agent.services.workflow_runtime.errors import OptimisticConcurrencyError
from tests.bpmn.test_execution_recovery import advance_bounded
from tests.bpmn.test_execution_routing import compile_request, execution_plan, xor_xml
from tests.test_native_graph_runtime import runtime


def event(kind, key, *, now=100.0):
    return CanonicalWorkflowEvent.build(
        tenant_id="tenant-a",
        workflow_id="process",
        run_id="audit-run",
        event_type=kind,
        actor="hub",
        dedupe_key=key,
        correlation_id="audit-run",
        causation_id="control",
        occurred_at=now,
        payload={},
    )


def test_audit_append_between_checkpoints_does_not_break_worker_result_acceptance():
    hub, queue, handler, _, _, stores = runtime()
    request = NativeGraphRequest(
        execution_plan(compile_request(xor_xml())), "audit-run", "control", input_data={"approved": True}
    )
    result = hub.start(request)
    result = hub.advance(request)
    result = hub.advance(request)
    stores["events"].append(
        event("workflow.step.authorization_checked", "worker-auth"), expected_sequence=result.event_cursor
    )
    result = advance_bounded(hub, request, result)
    assert result.status == "completed"
    assert handler.calls == ["yes_task"]
    assert "workflow.step.authorization_checked" in {item.event_type for item in hub.stream(request)}


def test_replay_retains_first_event_timestamp_and_rejects_changed_content():
    store = InMemoryEventStore()
    first = append_native_event(store, event("workflow.step.completed", "complete"), observed_sequence=0)
    replay = append_native_event(store, event("workflow.step.completed", "complete", now=150.0), observed_sequence=0)
    assert replay == first
    with pytest.raises(OptimisticConcurrencyError, match="payload_conflict"):
        append_native_event(
            store, replace(event("workflow.step.completed", "complete"), payload={"changed": True}), observed_sequence=0
        )


def test_control_event_cannot_be_rebased_as_an_observation():
    store = InMemoryEventStore()
    store.append(event("workflow.run.cancelled", "cancel"), expected_sequence=0)
    with pytest.raises(OptimisticConcurrencyError, match="stale_control_state"):
        append_native_event(store, event("workflow.step.completed", "complete"), observed_sequence=0)


def test_catchup_is_bounded():
    store = InMemoryEventStore()
    for index in range(257):
        store.append(event("workflow.step.authorization_checked", f"auth-{index}"), expected_sequence=index)
    with pytest.raises(OptimisticConcurrencyError, match="catchup_unavailable"):
        append_native_event(store, event("workflow.step.completed", "complete"), observed_sequence=0)


def test_gateway_observation_retries_a_concurrent_graph_event():
    class RacingStore(InMemoryEventStore):
        def append(self, value, *, expected_sequence):
            if not self.list_events(tenant_id=value.tenant_id, run_id=value.run_id):
                super().append(event("workflow.step.completed", "complete"), expected_sequence=0)
            return super().append(value, expected_sequence=expected_sequence)

    stored = append_gateway_observation(RacingStore(), event("workflow.step.authorization_checked", "auth"))
    assert stored.sequence == 2


def test_gateway_observation_replay_binds_content_and_preserves_time():
    store = InMemoryEventStore()
    original = event("workflow.step.authorization_checked", "auth")
    first = append_gateway_observation(store, original)
    assert append_gateway_observation(store, replace(original, occurred_at=150.0)) == first
    with pytest.raises(OptimisticConcurrencyError, match="payload_conflict"):
        append_gateway_observation(store, replace(original, payload={"allowed": False}))


@pytest.mark.parametrize(
    "kind,actor", [("workflow.run.cancelled", "hub"), ("workflow.step.authorization_checked", "worker")]
)
def test_gateway_cannot_rebase_control_transitions_or_worker_authored_events(kind, actor):
    with pytest.raises(ValueError, match="observation_event_required"):
        append_gateway_observation(InMemoryEventStore(), replace(event(kind, "event"), actor=actor))


def test_gateway_observation_contention_is_bounded():
    class BusyStore(InMemoryEventStore):
        def append(self, value, *, expected_sequence):
            raise OptimisticConcurrencyError("event_sequence_conflict:concurrent")

    with pytest.raises(OptimisticConcurrencyError, match="contention_exceeded"):
        append_gateway_observation(BusyStore(), event("workflow.step.authorization_checked", "auth"))
