"""SQL retry observations reopen cancellation without manufacturing execution."""

from dataclasses import replace

import pytest

from agent.services.native_graph_models import NativeRunState
from tests.bpmn.completion_helpers import SimulatedHubCrash, crash_once, linear_request
from tests.bpmn.completion_helpers import harness as harness
from tests.test_native_graph_runtime import signed_control


def issue(harness, result, kind, command_id):
    return signed_control(keys=harness.keys, checkpoint=result.checkpoint, command_type=kind,
                          step_id='work', command_id=command_id)


def edge_states(harness, request):
    return {event.payload['edge_id']: event.payload['edge_status'] for event in harness.hub.stream(request)
            if event.event_type.startswith('workflow.bpmn.edge.')}


@pytest.mark.parametrize('crash', [False, True])
def test_cancelled_approval_retry_reopens_edges_after_sql_restart(harness, monkeypatch, crash):
    request = linear_request(user_task=True)
    harness.hub.start(request)
    waiting = harness.finish(request)
    assert waiting.status == 'waiting_for_approval'
    cancelled = harness.hub.resume(request, command=issue(harness, waiting, 'cancel', 'synthetic-cancel'))
    assert set(edge_states(harness, request).values()) == {'cancelled'}
    retry = issue(harness, cancelled, 'retry', 'synthetic-retry')
    if crash:
        crash_once(monkeypatch, harness.stores['events'], 'append_fenced', after=True,
                   predicate=lambda event, **kwargs: event.event_type == 'workflow.bpmn.edge.pending')
        with pytest.raises(SimulatedHubCrash):
            harness.hub.resume(request, command=retry)
    harness.now += 1
    harness.restart()
    reopened = harness.hub.resume(request, command=retry, admitted_replay=crash)
    assert reopened.status == 'waiting_for_approval'
    assert edge_states(harness, request) == {'begin': 'selected', 'next': 'pending', 'finish': 'pending'}
    assert reopened.checkpoint.state.runtime_metadata['bpmn_edge_epoch'] == 1
    before = list(harness.hub.stream(request))
    assert harness.hub.resume(request, command=retry, admitted_replay=True).checkpoint == reopened.checkpoint
    assert list(harness.hub.stream(request)) == before
    assert len([event for event in before if event.event_type == 'workflow.bpmn.edge.pending']) == 3
    assert harness.queue.submissions == []


def test_retry_preserves_accepted_service_result_and_completed_edge(harness):
    request = linear_request()
    request = replace(request, plan=replace(request.plan, budget=replace(request.plan.budget, max_attempts=3)))
    harness.dispatch_first(request)
    running = harness.hub.advance(request)
    assert 'work' in running.completed_node_ids
    cancelled = harness.hub.resume(request, command=issue(harness, running, 'cancel', 'synthetic-cancel-after-work'))
    reopened = harness.hub.resume(request, command=issue(harness, cancelled, 'retry', 'synthetic-retry-after-work'))
    assert reopened.status == 'running'
    assert edge_states(harness, request)['begin'] == 'completed'
    completed = harness.finish(request)
    assert completed.status == 'completed'
    assert harness.handler.calls.count('work') == 1
    assert set(edge_states(harness, request).values()) == {'completed'}


@pytest.mark.parametrize('value', [True, -1, '1', None])
def test_edge_epoch_is_not_coerced_from_untrusted_checkpoint_data(value):
    state = NativeRunState().to_workflow_state(secret_refs=())
    with pytest.raises(ValueError, match='bpmn_edge_epoch_invalid'):
        NativeRunState.from_workflow_state(replace(state, runtime_metadata={**state.runtime_metadata,
                                                                         'bpmn_edge_epoch': value}))
