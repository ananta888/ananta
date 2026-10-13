"""Source-bound expanded edge identities are separate from event correlations."""

from collections import defaultdict
from dataclasses import replace

import pytest

from agent.services.bpmn_workflow_preflight import workflow_start_plan
from agent.services.caseflow_agent_collaboration_trace_projection_service import (
    CaseflowAgentCollaborationTraceProjectionService,
)
from agent.services.workflow_control_bindings import InMemoryWorkflowControlBindingStore, WorkflowControlRunBinding
from agent.visual_process.edge_catalog_contract import CASEFLOW_EDGE_CATALOG_METADATA_KEY, build_caseflow_edge_catalog
from tests.bpmn.test_xml_regions import compile_xml, loop_xml, subprocess_xml


def binding(xml):
    graph, request = compile_xml(xml)
    plan = workflow_start_plan(request, tenant_id='synthetic-caseflow')
    return graph, WorkflowControlRunBinding(tenant_id=plan.tenant_id, subject_id='synthetic-owner',
        workflow_id=request.workflow_id, run_id='synthetic-caseflow-run', runtime_id='ananta-native',
        plan_hash=plan.plan_hash, policy_version=plan.policy_version, checkpoint_id='synthetic-checkpoint',
        request=request, execution_plan=plan.to_dict())


@pytest.mark.parametrize('xml', [loop_xml(), subprocess_xml()])
def test_all_expanded_edges_keep_exact_source_lineage_without_invented_event_evidence(xml):
    graph, value = binding(xml)
    projected = CaseflowAgentCollaborationTraceProjectionService(InMemoryWorkflowControlBindingStore()).project(
        binding=value, raw_events=[], source_revision=1)
    assert projected['catalog_verification_status'] == 'verified'
    assert projected['verification_status'] == 'unverified'
    assert {edge['edge_id'] for edge in projected['edges']} == {edge.id for edge in graph.edges}
    for edge in projected['edges']:
        original = next(item for item in graph.edges if item.id == edge['edge_id'])
        provenance = edge['bpmn_provenance']
        assert provenance['activation_id'] == original.id
        assert (provenance['source_step_id'], provenance['target_step_id']) == (original.source, original.target)
        assert provenance['element_id'] == original.metadata['bpmn_activation_origin']['source_id']
        assert provenance['scope'] == original.metadata['bpmn_activation_origin']['scope']
        assert provenance['activation_count'] >= 1
        assert edge['verification_status'] == 'unverified' and edge['event_refs'] == []


def test_changed_binding_cannot_supply_trusted_bpmn_lineage():
    _, value = binding(loop_xml())
    value = replace(value, plan_hash='0' * 64)
    projected = CaseflowAgentCollaborationTraceProjectionService(InMemoryWorkflowControlBindingStore()).project(
        binding=value, raw_events=[])
    assert projected['catalog_verification_status'] == 'unverified'
    assert projected['reason_code'] == 'caseflow_bpmn_binding_invalid'
    assert projected['edges'] == []


def test_catalog_cannot_rebind_an_edge_to_another_valid_predecessor():
    _, value = binding(loop_xml())
    catalog = value.request.metadata[CASEFLOW_EDGE_CATALOG_METADATA_KEY]
    incoming = defaultdict(list)
    for edge in catalog['edges']:
        incoming[edge['target_step_id']].append(edge)
    pair = next(edges for edges in incoming.values() if len(edges) > 1)
    changed = [dict(edge) for edge in catalog['edges']]
    altered_edge = next(edge for edge in changed if edge['edge_id'] == pair[0]['edge_id'])
    altered_edge['source_step_id'] = pair[1]['source_step_id']
    request = replace(value.request, metadata={**value.request.metadata,
                      CASEFLOW_EDGE_CATALOG_METADATA_KEY: build_caseflow_edge_catalog(changed)})
    plan = workflow_start_plan(request, tenant_id=value.tenant_id)
    value = replace(value, request=request, plan_hash=plan.plan_hash, execution_plan=plan.to_dict())
    projected = CaseflowAgentCollaborationTraceProjectionService(InMemoryWorkflowControlBindingStore()).project(
        binding=value, raw_events=[])
    assert projected['reason_code'] == 'caseflow_bpmn_binding_invalid'
    assert projected['edges'] == []
