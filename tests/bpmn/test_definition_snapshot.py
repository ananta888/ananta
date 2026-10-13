"""Owner-only immutable source reads; no runtime execution or secret payload projection."""

from dataclasses import replace

import pytest
from flask import Flask

from agent.auth import generate_token
from agent.config import settings
from agent.routes.visual_process import vp_bp
from agent.services.bpmn_definition_snapshot import BpmnDefinitionSnapshotError, project_bpmn_definition
from agent.services.bpmn_workflow_preflight import workflow_start_plan
from agent.services.workflow_control_bindings import WorkflowControlRunBinding
from agent.services.workflow_route_authorization_service import (
    WorkflowRoutePrincipal,
    workflow_route_authorization_service,
)
from agent.visual_process.blueprint_mapper import graph_to_workflow_request
from agent.visual_process.bpmn_adapter import import_bpmn_xml
from tests.bpmn_container.fixtures import service
from tests.test_workflow_control_composition import RecordingBackend, _bound_facade


@pytest.fixture
def snapshot():
    graph = import_bpmn_xml(service()).graph
    graph.id = 'workflow-control-composition'
    request = graph_to_workflow_request(graph, policy_scope={'source': 'synthetic-test'})
    plan = workflow_start_plan(request, tenant_id='tenant-a')
    binding = WorkflowControlRunBinding(tenant_id='tenant-a', subject_id='owner-a', workflow_id=request.workflow_id,
        run_id='synthetic-definition-run', runtime_id='ananta-native', plan_hash=plan.plan_hash,
        policy_version=plan.policy_version, checkpoint_id='synthetic-checkpoint', request=request,
        execution_plan=plan.to_dict())
    backend = RecordingBackend()
    facade, owner, _ = _bound_facade(backend)
    facade.bindings.put(binding)
    return binding, facade, owner, backend


def test_owner_reads_bound_source_without_querying_or_starting_runtime(snapshot):
    binding, _, owner, backend = snapshot
    value = owner.get_bpmn_definition(binding.workflow_id)
    assert value['bpmn_xml'] == service()
    assert value['plan_hash'] == binding.plan_hash
    assert value['definition_hash'] == binding.execution_plan['metadata']['bpmn_definition_hash']
    assert set(value) == {'schema', 'workflow_id', 'run_id', 'plan_hash', 'definition_hash',
                         'source_sha256', 'bpmn_xml'}
    assert backend.starts == backend.queries == 0


@pytest.mark.parametrize('principal', [WorkflowRoutePrincipal('tenant-a', 'other-owner'),
                                    WorkflowRoutePrincipal('other-tenant', 'owner-a')])
def test_foreign_owner_or_tenant_cannot_read_source(snapshot, principal):
    binding, facade, _, backend = snapshot
    with pytest.raises(PermissionError, match='workflow_run_not_found'):
        facade.bind(principal).get_bpmn_definition(binding.workflow_id)
    assert backend.starts == backend.queries == 0


@pytest.mark.parametrize('change', ['source', 'source_and_hash', 'hash', 'plan', 'definition'])
def test_mutated_source_or_immutable_binding_cannot_be_projected(snapshot, change):
    binding, _, _, _ = snapshot
    metadata = dict(binding.request.metadata)
    if change in {'source', 'source_and_hash'}:
        metadata['bpmn_source_xml'] += '\n'
        if change == 'source_and_hash':
            import hashlib

            metadata['bpmn_source_sha256'] = hashlib.sha256(metadata['bpmn_source_xml'].encode()).hexdigest()
    elif change == 'hash':
        metadata['bpmn_source_sha256'] = '0' * 64
    elif change == 'definition':
        metadata['ananta.visual_process.definition_hash'] = '0' * 64
    altered = replace(binding, request=replace(binding.request, metadata=metadata))
    if change == 'plan':
        altered = replace(altered, plan_hash='0' * 64)
    with pytest.raises(BpmnDefinitionSnapshotError, match='workflow_bpmn_definition_binding_invalid'):
        project_bpmn_definition(altered)


def test_definition_http_requires_real_auth_and_owner_before_source_projection(snapshot, monkeypatch):
    binding, facade, _, _ = snapshot
    monkeypatch.setattr('agent.routes.workflow_control_security.get_workflow_backend_control_facade', lambda: facade)
    workflow_route_authorization_service.clear()
    workflow_route_authorization_service.reserve(binding.workflow_id, WorkflowRoutePrincipal('tenant-a', 'owner-a'))
    app = Flask(__name__)
    app.config.update(TESTING=True, AGENT_TOKEN=None)
    app.register_blueprint(vp_bp)
    endpoint = '/api/visual-process/workflow/' + binding.workflow_id + '/bpmn-definition'
    client = app.test_client()
    assert client.get(endpoint).status_code == 401
    for subject, tenant, expected in [('owner-a', 'tenant-a', 200), ('other-owner', 'tenant-a', 404),
                                      ('owner-a', 'other-tenant', 404)]:
        token = generate_token({'sub': subject, 'tenant_id': tenant, 'role': 'user'}, settings.secret_key)
        response = client.get(endpoint, headers={'Authorization': 'Bearer ' + token})
        assert response.status_code == expected
        if expected == 200:
            assert response.json['bpmn_xml'] == service()
        else:
            assert 'bpmn_xml' not in response.json
