"""Exact timestamp-preserving replay of observations already covered by a cursor."""

from dataclasses import replace

import pytest
from sqlmodel import SQLModel, create_engine

from agent.services.native_graph_event_appender import append_native_event
from agent.services.workflow_runtime import InMemoryEventStore, SQLAlchemyEventStore
from agent.services.workflow_runtime.errors import OptimisticConcurrencyError
from agent.services.workflow_runtime.events import CanonicalWorkflowEvent


@pytest.mark.parametrize('sql', [False, True])
def test_observed_exact_replay_keeps_timestamp_and_rejects_changed_intent(sql, tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'events.sqlite')) if sql else None
    if engine is not None:
        SQLModel.metadata.create_all(engine)
    store = SQLAlchemyEventStore(engine) if sql else InMemoryEventStore()
    event = CanonicalWorkflowEvent.build(tenant_id='test', workflow_id='workflow', run_id='run', step_id='target',
        event_type='workflow.bpmn.edge.selected', actor='hub', dedupe_key='synthetic-edge-choice',
        correlation_id='synthetic-correlation', causation_id='synthetic-control',
        payload={'edge_id': 'edge', 'edge_status': 'selected'}, occurred_at=100)
    stored = append_native_event(store, event, observed_sequence=0)
    replay = replace(event, occurred_at=200)
    recovered = append_native_event(store, replay, observed_sequence=stored.sequence)
    assert recovered == stored and recovered.occurred_at == 100
    with pytest.raises(OptimisticConcurrencyError, match='dedupe_key_payload_conflict'):
        append_native_event(store, replace(replay, payload={'edge_id': 'different'}),
                            observed_sequence=stored.sequence)
    assert len(store.list_events(tenant_id='test', run_id='run')) == 1
    if engine is not None:
        engine.dispose()


def test_known_replay_cannot_bypass_expired_control_lease():
    from tests.bpmn.test_run_lease import setup

    clock, leases, request = setup()
    store = InMemoryEventStore()
    event = CanonicalWorkflowEvent.build(tenant_id=request.plan.tenant_id, workflow_id=request.plan.workflow_id,
        run_id=request.run_id, step_id='target', event_type='workflow.bpmn.edge.selected', actor='hub',
        dedupe_key='synthetic-expired-replay', payload={'edge_id': 'edge'}, occurred_at=100,
        correlation_id='synthetic', causation_id=request.control_task_id)
    with leases.acquire(request) as lease:
        first = append_native_event(store, event, observed_sequence=0, lease=lease)
        clock[0] = 131
        with pytest.raises(OptimisticConcurrencyError, match='lease_stale'):
            append_native_event(store, replace(event, occurred_at=200), observed_sequence=first.sequence, lease=lease)
    assert len(store.list_events(tenant_id=request.plan.tenant_id, run_id=request.run_id)) == 1
