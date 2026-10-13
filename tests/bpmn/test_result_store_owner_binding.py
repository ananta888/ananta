"""Exact registered Worker/store binding, actual SQL/private stores and signed HTTP."""

# ruff: noqa: F811 -- pytest fixture imports are explicit and intentionally reused.

import base64
import copy
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlmodel import Session

from agent.db_models import AgentInfoDB
from agent.repositories.core import AgentRepository
from agent.routes.tasks.autopilot_dispatch_policy import resolve_target_worker_for_task
from agent.services.agent_registry_service import AgentRegistryService
from agent.services.native_graph_orchestration_service import NativeGraphRequest
from agent.services.native_result_maintenance_owner import require_registered_result_store_owner
from agent.services.workflow_worker_service_auth import STRICT_WORKER_REGISTRATION_PROVENANCE
from ananta_contracts.native_result_maintenance import (
    MAINTENANCE_CAPABILITY,
    MAINTENANCE_TASK_KIND,
    RESULT_STORE_BINDING_LIMIT,
    maintenance_input,
)
from ananta_contracts.native_task_execution import (
    NATIVE_TASK_EXECUTION_SCHEMA,
    sign_native_execution_request,
    verify_native_execution_request,
)
from ananta_contracts.runtime_authorization_crypto import Ed25519SigningKeyRing, Ed25519VerificationKeyRing
from tests.bpmn.test_native_task_execution_http import signed as signed  # noqa: F401
from tests.bpmn.test_result_maintenance_node import cleanup as cleanup  # noqa: F401
from tests.bpmn.test_result_maintenance_node import maintenance_plan
from tests.bpmn.test_result_maintenance_scope import submission as submission  # noqa: F401
from worker.runtime.native_graph.http_execution import create_native_execution_blueprint
from worker.runtime.native_graph.result_maintenance_node import NativeResultMaintenanceHandler
from worker.runtime.native_graph.result_store_runtime import configured_native_result_store_for_task
from worker.runtime.workflow_adapter_worker_profile import NativeGraphWorkerProfile

OWNER = {'worker_id': 'synthetic-worker', 'worker_url': 'http://synthetic-worker:5000',
         'store_binding_id': 'synthetic-store-v1'}


def registered_worker(**changes):
    return SimpleNamespace(name=OWNER['worker_id'], url=OWNER['worker_url'], role='worker', status='online',
        registration_validated=True, registration_provenance=STRICT_WORKER_REGISTRATION_PROVENANCE,
        authorized_capabilities=['workflow.adapter.native', MAINTENANCE_CAPABILITY], last_seen=time.time(),
        execution_limits={RESULT_STORE_BINDING_LIMIT: OWNER['store_binding_id']}, **changes)


def bound_command(command):
    inputs = copy.deepcopy(command.input_data)
    inputs['workflow_input'] = {'batch_size': 100, 'result_store_owner': dict(OWNER)}
    return replace(command, input_data=inputs)


def test_actual_hub_sql_scope_pins_registered_owner_before_queue_ingestion(submission, monkeypatch):
    worker = registered_worker()
    with Session(submission.database) as session, session.begin():
        session.add(AgentInfoDB(**vars(worker)))
    agents = AgentRepository()
    monkeypatch.setattr('agent.repositories.core.engine', submission.database)
    from agent.repositories.tasks import TaskRepository

    monkeypatch.setattr('agent.services.native_result_maintenance_scope.get_repository_registry',
        lambda: SimpleNamespace(task_repo=TaskRepository(), agent_repo=agents))
    command = bound_command(submission.command)
    receipt = submission.adapter.submit(command)
    task = submission.repository.get_by_id(receipt.hub_task_id)
    assert task.assigned_agent_url == OWNER['worker_url'] and task.project_id == 'project-1'
    persisted_inputs = task.worker_execution_context['native_node_command']['input_data']['workflow_input']
    assert persisted_inputs['result_store_owner'] == OWNER
    assert submission.adapter.submit(command).accepted
    with Session(submission.database) as session, session.begin():
        current = session.get(AgentInfoDB, OWNER['worker_url'])
        current.execution_limits = {RESULT_STORE_BINDING_LIMIT: 'rotated-store'}
        session.add(current)
    with pytest.raises(ValueError, match='registered_owner_unavailable'):
        submission.adapter.submit(command)
    assert len(submission.queue.ingested_values) == 1


@pytest.mark.parametrize('field,value', [
    ('role', 'hub'), ('status', 'offline'), ('registration_validated', False),
    ('registration_provenance', 'legacy'), ('authorized_capabilities', ['workflow.adapter.native']),
    ('last_seen', 1), ('execution_limits', {RESULT_STORE_BINDING_LIMIT: 'other-store'}),
    ('name', 'other-worker'), ('url', 'http://other-worker:5000'),
])
def test_owner_revalidation_refuses_stale_foreign_or_unadmitted_registry_record(field, value):
    worker = registered_worker()
    setattr(worker, field, value)
    with pytest.raises(ValueError, match='registered_owner_unavailable'):
        require_registered_result_store_owner(worker, OWNER, now=time.time())


def test_routing_selects_exact_owner_and_never_falls_back_to_other_store():
    worker = registered_worker()
    other = registered_worker()
    other.name, other.url = 'other-worker', 'http://other-worker:5000'
    task = SimpleNamespace(task_kind=MAINTENANCE_TASK_KIND, assigned_agent_url=None,
        worker_execution_context={'runtime_path': 'native_graph_node', 'native_node_command': {
            'input_data': {'workflow_input': {'batch_size': 100, 'result_store_owner': OWNER}}}})
    target, cursor, assign, reason = resolve_target_worker_for_task(
        task=task, workers=[other, worker], worker_cursor=99)
    assert target is worker and cursor == 99 and assign and reason == 'native_result_store_owner_binding'
    target, _, assign, reason = resolve_target_worker_for_task(task=task, workers=[other], worker_cursor=99)
    assert target is None and not assign and reason == 'native_result_store_owner_unavailable'


def test_signer_and_independent_verifier_reject_another_recipient(signed):
    task = copy.deepcopy(signed[0]['task'])
    task['task_kind'] = MAINTENANCE_TASK_KIND
    command = task['worker_execution_context']['native_node_command']
    command['node']['task_kind'] = MAINTENANCE_TASK_KIND
    command['input_data'] = {'workflow_input': {'batch_size': 100, 'result_store_owner': OWNER}}
    signer = Ed25519SigningKeyRing({'synthetic': base64.b64encode(b's' * 32).decode()}, active_key_id='synthetic')
    with pytest.raises(ValueError, match='recipient_mismatch'):
        sign_native_execution_request(task, key_ring=signer, worker_id='other-worker',
            worker_url='http://other-worker:5000', expires_at=time.time() + 30)
    value = sign_native_execution_request(task, key_ring=signer, worker_id=OWNER['worker_id'],
        worker_url=OWNER['worker_url'], expires_at=time.time() + 30)
    # Even a synthetically re-signed wrong-recipient request must fail the independent consumer check.
    payload = {key: val for key, val in value.items() if key not in {'key_id', 'signature'}}
    payload.update(worker_id='other-worker', worker_url='http://other-worker:5000')
    key, signature = signer.sign(namespace=NATIVE_TASK_EXECUTION_SCHEMA, payload=payload)
    with pytest.raises(ValueError, match='recipient_mismatch'):
        verify_native_execution_request({**payload, 'key_id': key, 'signature': signature},
            key_ring=signed[1], worker_id='other-worker', worker_url='http://other-worker:5000')


@pytest.mark.parametrize('store_binding', ['synthetic-store-v1', 'another-store'])
def test_signed_http_checks_store_binding_before_any_private_store_access(cleanup, tmp_path, store_binding):
    cleanup.queue.runtime._handler = NativeResultMaintenanceHandler(scope=cleanup.handler._scope,
        store_factory=cleanup.factory, store_binding_id=OWNER['store_binding_id'])
    request = NativeGraphRequest(maintenance_plan(), 'bound-maintenance-run', 'synthetic-control',
        input_data={'batch_size': 100, 'result_store_owner': OWNER})
    cleanup.hub.start(request)
    command = cleanup.queue.submissions[0]
    task = {'id': 'native-' + command.command_id, 'tenant_id': command.tenant_id,
        'project_id': 'synthetic-project', 'task_kind': MAINTENANCE_TASK_KIND,
        'derivation_reason': 'native_graph_hub_delegation',
        'worker_execution_context': {'schema': 'ananta.native_graph_worker_context.v1',
            'runtime_path': 'native_graph_node', 'native_node_command': command.to_dict()}}
    signer = Ed25519SigningKeyRing({'synthetic': base64.b64encode(b's' * 32).decode()}, active_key_id='synthetic')
    value = sign_native_execution_request(task, key_ring=signer, worker_id=OWNER['worker_id'],
        worker_url=OWNER['worker_url'], expires_at=time.time() + 30)
    app = Flask(__name__)
    path = tmp_path / 'bound-private' / 'results.sqlite3'
    path.parent.mkdir(mode=0o700)
    app.config.update(ROLE='worker', ANANTA_NATIVE_RESULT_STORE_PATH=str(path))
    app.extensions['workflow_adapter_worker_registration'] = {'execution_limits': {
        RESULT_STORE_BINDING_LIMIT: store_binding}}
    calls = []
    app.register_blueprint(create_native_execution_blueprint(token_provider=lambda: 'x' * 40,
        recipient_provider=lambda: (OWNER['worker_id'], OWNER['worker_url']),
        verification_provider=lambda: Ed25519VerificationKeyRing.from_mapping(signer.verification_mapping()),
        consumer=lambda task: calls.append(task) or {'status': 'completed'},
        result_store_resolver=configured_native_result_store_for_task))
    response = app.test_client().post('/api/internal/native-tasks/execute', json=value,
        headers={'Authorization': 'Bearer ' + 'x' * 40})
    if store_binding != OWNER['store_binding_id']:
        assert response.status_code == 503 and calls == [] and not path.exists()
    else:
        assert response.status_code == 200 and calls == [task] and path.exists()


def test_registration_preserves_store_binding_and_typed_worker_profile_accepts_it():
    cfg = {'enabled': True, 'allowed_task_types': [MAINTENANCE_TASK_KIND],
        'capabilities': [MAINTENANCE_CAPABILITY], 'result_maintenance': True,
        'result_store_binding_id': OWNER['store_binding_id']}
    assert NativeGraphWorkerProfile.model_validate(cfg).result_store_binding_id == OWNER['store_binding_id']
    normalized, reason, status = AgentRegistryService().validate_registration_payload({
        'name': OWNER['worker_id'], 'url': OWNER['worker_url'], 'role': 'worker',
        'capabilities': ['workflow.adapter.native', MAINTENANCE_CAPABILITY],
        'execution_limits': {RESULT_STORE_BINDING_LIMIT: OWNER['store_binding_id']}}, registration_token=None)
    assert status == 200 and reason is None
    assert normalized['execution_limits'][RESULT_STORE_BINDING_LIMIT] == OWNER['store_binding_id']


def test_productive_runtime_registration_and_registration_payload_keep_binding(monkeypatch):
    from agent import utils
    from worker.runtime.workflow_adapter_runtime_composition import build_workflow_adapter_worker_runtime

    config = {'worker_runtime': {'native_graph': {'enabled': True,
        'allowed_task_types': [MAINTENANCE_TASK_KIND], 'capabilities': [MAINTENANCE_CAPABILITY],
        'result_maintenance': True, 'result_store_binding_id': OWNER['store_binding_id']}}}
    runtime = build_workflow_adapter_worker_runtime(agent_config=config, client=SimpleNamespace(),
        native_executor=SimpleNamespace(), native_authorization_verifier=SimpleNamespace())
    metadata = runtime.registration_metadata()
    assert 'workflow.adapter.native' in metadata['capabilities']
    assert metadata['execution_limits'][RESULT_STORE_BINDING_LIMIT] == OWNER['store_binding_id']
    calls = []
    monkeypatch.setattr(utils, '_http_post', lambda url, data, **options: calls.append(data) or {})
    assert utils.register_with_hub('http://synthetic-hub:5000', OWNER['worker_id'], 5000, 'x' * 40,
        capabilities=metadata['capabilities'], execution_limits=metadata['execution_limits'])
    assert calls[0]['execution_limits'][RESULT_STORE_BINDING_LIMIT] == OWNER['store_binding_id']


@pytest.mark.parametrize('binding', [None, 'rotated-store'])
def test_missing_or_changed_registration_cannot_relabel_a_cached_store(binding):
    app = Flask(__name__)
    cached = object()
    app.extensions['native_result_store'] = cached
    app.extensions['native_result_store_binding'] = OWNER['store_binding_id']
    app.extensions['workflow_adapter_worker_registration'] = {'execution_limits': {
        RESULT_STORE_BINDING_LIMIT: binding}}
    task = {'task_kind': MAINTENANCE_TASK_KIND, 'worker_execution_context': {'native_node_command': {
        'input_data': {'workflow_input': {'batch_size': 100, 'result_store_owner': OWNER}}}}}
    with app.app_context(), pytest.raises(ValueError, match='binding_mismatch'):
        configured_native_result_store_for_task(task)
    assert app.extensions['native_result_store'] is cached


def test_bound_handler_refuses_another_store_before_factory(cleanup):
    cleanup.queue.runtime._handler = NativeResultMaintenanceHandler(scope=cleanup.handler._scope,
        store_factory=cleanup.factory, store_binding_id='other-store')
    request = NativeGraphRequest(maintenance_plan(), 'wrong-store-maintenance-run', 'synthetic-control',
        input_data={'batch_size': 100, 'result_store_owner': OWNER})
    result = cleanup.hub.start(request)
    if result.status == 'running':
        result = cleanup.hub.advance(request)
    assert result.status == 'failed'
    cleanup.factory.assert_not_called()


def test_bound_operation_executes_actual_scoped_expiry_on_the_declared_store(cleanup):
    cleanup.queue.runtime._handler = NativeResultMaintenanceHandler(scope=cleanup.handler._scope,
        store_factory=cleanup.factory, store_binding_id=OWNER['store_binding_id'])
    request = NativeGraphRequest(maintenance_plan(), 'bound-maintenance-run', 'synthetic-control',
        input_data={'batch_size': 100, 'result_store_owner': OWNER})
    result = cleanup.hub.start(request)
    if result.status != 'completed':
        result = cleanup.hub.advance(request)
    assert result.status == 'completed'
    cleanup.factory.assert_called_once()
    assert cleanup.store.expire_results(batch_size=100, tenant_id='tenant-a', project_id='synthetic-project') == 0


@pytest.mark.parametrize('owner', [None, {}, {**OWNER, 'extra': 'not admitted'},
    {**OWNER, 'store_binding_id': True}, {**OWNER, 'worker_url': 'http://user:password@worker:5000'},
    {**OWNER, 'worker_url': 'http://worker:5000/arbitrary/path'}])
def test_closed_owner_inputs_refuse_invalid_bindings(owner):
    with pytest.raises(ValueError):
        maintenance_input({'batch_size': 100, 'result_store_owner': owner})
