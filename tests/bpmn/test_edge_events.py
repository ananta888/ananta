"""Real Hub routing/events and unchanged Worker delegation under synthetic policies."""

from dataclasses import replace

import pytest

from agent.services.caseflow_agent_collaboration_trace_projection_service import (
    CaseflowAgentCollaborationTraceProjectionService,
)
from agent.services.native_graph_orchestration_service import NativeGraphRequest
from agent.services.workflow_control_bindings import InMemoryWorkflowControlBindingStore, WorkflowControlRunBinding
from tests.bpmn.test_execution_routing import compile_request, execution_plan, xor_xml
from tests.test_native_graph_runtime import runtime, signed_control


@pytest.mark.parametrize('approved,chosen,rejected', [(True, 'yes', 'no'), (False, 'no', 'yes')])
def test_real_xor_edge_decisions_are_exact_and_terminal_replay_has_no_duplicate_events(approved, chosen, rejected):
    hub, queue, _, *_ = runtime()
    workflow = compile_request(xor_xml())
    workflow = replace(workflow, metadata={**workflow.metadata, 'policy_version': 'policy-v1'})
    plan = execution_plan(workflow)
    request = NativeGraphRequest(plan, 'synthetic-edge-run', 'synthetic-control', input_data={'approved': approved})
    result = hub.start(request)
    for _ in range(30):
        if result.status == 'completed':
            break
        result = hub.advance(request)
    assert result.status == 'completed'
    events = list(hub.stream(request))
    edges = [event for event in events if event.event_type.startswith('workflow.bpmn.edge.')]
    assert any(event.payload['edge_id'] == chosen and event.payload['edge_status'] == 'completed' for event in edges)
    assert any(event.payload['edge_id'] == rejected and event.payload['edge_status'] == 'skipped' for event in edges)
    assert not any(event.payload['edge_id'] == rejected and event.payload['edge_status'] == 'selected'
                   for event in edges)
    assert all(event.actor == 'hub' and event.payload['plan_hash'] == plan.plan_hash for event in edges)
    before = {event.event_id for event in edges}
    hub.advance(request)
    after = {event.event_id for event in hub.stream(request) if event.event_type.startswith('workflow.bpmn.edge.')}
    assert after == before
    assert len(queue.submissions) == (1 if approved else 2)
    binding = WorkflowControlRunBinding(tenant_id=plan.tenant_id, subject_id='synthetic-owner',
        workflow_id=plan.workflow_id, run_id=request.run_id, runtime_id=hub.runtime_id, plan_hash=plan.plan_hash,
        policy_version=plan.policy_version, checkpoint_id=result.checkpoint.checkpoint_id,
        request=workflow, execution_plan=plan.to_dict())
    service = CaseflowAgentCollaborationTraceProjectionService(InMemoryWorkflowControlBindingStore())
    trace = service.project(binding=binding, raw_events=[event.to_dict() for event in events], source_revision=1)
    assert trace['verification_status'] == 'verified'
    assert next(edge for edge in trace['edges'] if edge['edge_id'] == chosen)['edge_status'] == 'completed'
    assert next(edge for edge in trace['edges'] if edge['edge_id'] == rejected)['edge_status'] == 'skipped'


def test_malformed_or_worker_claimed_hub_edge_facts_fail_closed():
    from ananta_contracts.bpmn_edge_event import valid_bpmn_edge_observation

    binding = dict(tenant_id='test', workflow_id='workflow', run_id='run', plan_hash='p',
                   definition_hash='d', directions={'edge': ('source', 'target')})
    event = {'schema': 'ananta.workflow_event.v1', 'actor': 'hub', 'tenant_id': 'test', 'workflow_id': 'workflow',
        'run_id': 'run', 'step_id': 'target', 'event_type': 'workflow.bpmn.edge.completed', 'payload': {
            'edge_id': 'edge', 'source_step_id': 'source', 'target_step_id': 'target', 'source_attempt': 0,
            'target_attempt': 1, 'edge_status': 'completed', 'plan_hash': 'p', 'definition_hash': 'd'}}
    assert valid_bpmn_edge_observation(event, **binding)
    for changed in ({**event, 'actor': 'native-worker'}, {**event, 'event_type': []},
                    {**event, 'payload': {**event['payload'], 'edge_id': {}}},
                    {**event, 'payload': {**event['payload'], 'source_step_id': 'foreign'}},
                    {**event, 'payload': {**event['payload'], 'target_attempt': True}}):
        assert not valid_bpmn_edge_observation(changed, **binding)


@pytest.mark.parametrize('accepted_first', [False, True])
def test_cancellation_settles_unfinished_edges_and_preserves_accepted_results(accepted_first):
    from tests.bpmn.test_execution_admission import diagram

    workflow = compile_request(diagram(
        '<startEvent id="start"/><serviceTask id="work"/><serviceTask id="after"/><endEvent id="end"/>'
        '<sequenceFlow id="begin" sourceRef="start" targetRef="work"/>'
        '<sequenceFlow id="next" sourceRef="work" targetRef="after"/>'
        '<sequenceFlow id="finish" sourceRef="after" targetRef="end"/>'
    ))
    workflow = replace(workflow, metadata={**workflow.metadata, 'policy_version': 'policy-v1'})
    plan = execution_plan(workflow)
    hub, queue, _, keys, *_ = runtime()
    request = NativeGraphRequest(plan, 'synthetic-cancel-edges', 'synthetic-control')
    result = hub.start(request)
    expected_dispatches = 2 if accepted_first else 1
    for _ in range(10):
        if len(queue.submissions) == expected_dispatches:
            break
        result = hub.advance(request)
    assert len(queue.submissions) == expected_dispatches
    command = signed_control(keys=keys, checkpoint=result.checkpoint, command_type='cancel',
                             step_id='__workflow__', command_id='synthetic-cancel-edges')
    result = hub.resume(request, command=command)
    assert result.status == 'cancelled'
    before = list(hub.stream(request))
    assert hub.advance(request).status == 'cancelled'
    assert list(hub.stream(request)) == before
    binding = WorkflowControlRunBinding(tenant_id=plan.tenant_id, subject_id='synthetic-owner',
        workflow_id=plan.workflow_id, run_id=request.run_id, runtime_id=hub.runtime_id, plan_hash=plan.plan_hash,
        policy_version=plan.policy_version, checkpoint_id=result.checkpoint.checkpoint_id,
        request=workflow, execution_plan=plan.to_dict())
    service = CaseflowAgentCollaborationTraceProjectionService(InMemoryWorkflowControlBindingStore())
    trace = service.project(binding=binding, raw_events=[event.to_dict() for event in before], source_revision=1)
    statuses = {edge['edge_id']: edge.get('edge_status') for edge in trace['edges']}
    assert trace['verification_status'] == 'verified'
    assert statuses == {'begin': 'completed' if accepted_first else 'cancelled',
                        'next': 'cancelled', 'finish': 'cancelled'}
    assert len(queue.submissions) == expected_dispatches


def test_cancellation_never_relabels_a_rejected_xor_branch():
    hub, queue, _, keys, *_ = runtime()
    plan = execution_plan(compile_request(xor_xml()))
    request = NativeGraphRequest(plan, 'synthetic-cancel-xor', 'synthetic-control', input_data={'approved': True})
    result = hub.start(request)
    for _ in range(10):
        if queue.submissions:
            break
        result = hub.advance(request)
    assert len(queue.submissions) == 1
    result = hub.resume(request, command=signed_control(keys=keys, checkpoint=result.checkpoint,
        command_type='cancel', step_id='__workflow__', command_id='synthetic-cancel-xor'))
    assert result.status == 'cancelled'
    events = list(hub.stream(request))
    assert any(event.payload.get('edge_id') == 'no' and event.event_type == 'workflow.bpmn.edge.skipped'
               for event in events)
    cancelled = {event.payload['edge_id'] for event in events if event.event_type == 'workflow.bpmn.edge.cancelled'}
    assert not cancelled.intersection({'no', 'child', 'no_join'})
