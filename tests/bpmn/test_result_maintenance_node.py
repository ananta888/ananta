"""Delegated finite owner cleanup through actual Native execution and SQLite store."""

import base64
import copy
import sqlite3
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from flask import Flask
from pydantic import ValidationError

from agent.services.native_graph_orchestration_service import NativeGraphRequest
from agent.services.workflow_runtime.execution_plan import ExecutionPlan
from ananta_contracts.native_task_execution import sign_native_execution_request
from ananta_contracts.runtime_authorization_crypto import Ed25519SigningKeyRing, Ed25519VerificationKeyRing
from tests.bpmn.test_native_task_execution_http import signed as signed
from tests.test_native_graph_runtime import runtime
from worker.runtime.native_graph.contracts import HubTaskReceipt
from worker.runtime.native_graph.handlers import UnavailableNativeNodeHandler
from worker.runtime.native_graph.http_execution import create_native_execution_blueprint
from worker.runtime.native_graph.result_maintenance_composition import build_result_maintenance_handlers
from worker.runtime.native_graph.result_maintenance_node import (
    MAINTENANCE_CAPABILITY,
    MAINTENANCE_TASK_KIND,
    MAINTENANCE_TOOL,
    NativeResultMaintenanceHandler,
)
from worker.runtime.native_graph.result_store import SQLiteNativeResultStore
from worker.runtime.native_graph.result_store_runtime import configured_native_result_store_for_task
from worker.runtime.workflow_adapter_runtime_composition import build_workflow_adapter_worker_runtime
from worker.runtime.workflow_adapter_worker_profile import NativeGraphWorkerProfile


def maintenance_plan():
    return ExecutionPlan.from_mapping({'tenant_id': 'tenant-a', 'plan_id': 'maintenance-v1',
        'workflow_id': 'maintenance-workflow', 'policy_version': 'policy-v1',
        'capabilities': [MAINTENANCE_CAPABILITY],
        'nodes': [{'id': 'expire-results', 'task_kind': MAINTENANCE_TASK_KIND,
            'required_capabilities': [MAINTENANCE_CAPABILITY], 'allowed_tools': [MAINTENANCE_TOOL],
            'side_effect_class': 'idempotent_write', 'metadata': {'operation_name': MAINTENANCE_TOOL}}],
        'budget': {'max_attempts': 1}})


@pytest.fixture
def cleanup(tmp_path, signed):  # noqa: F811 - imported pytest fixture
    now = [100]
    directory = tmp_path / 'private'
    directory.mkdir(mode=0o700)
    store = SQLiteNativeResultStore(directory / 'results.sqlite3', clock=lambda: now[0],
        retention_seconds=1, expire_on_open=False)
    old_task = copy.deepcopy(signed[0]['task'])
    old_task['tenant_id'] = 'tenant-a'
    old_task['worker_execution_context']['native_node_command']['tenant_id'] = 'tenant-a'
    store.reserve(old_task)
    store.publish(old_task, {'status': 'completed', 'output': 'synthetic-expired-private-answer'})
    other_project = copy.deepcopy(old_task)
    other_project.update(id='other-project-task', project_id='other-project')
    other_tenant = copy.deepcopy(old_task)
    other_tenant.update(id='other-tenant-task', tenant_id='other-tenant')
    other_tenant['worker_execution_context']['native_node_command']['tenant_id'] = 'other-tenant'
    for other in (other_project, other_tenant):
        store.reserve(other)
        store.publish(other, {'status': 'completed', 'output': 'foreign-scope-private-answer'})
    now[0] = 101
    hub, queue, _, _, ledger, _ = runtime()
    state = SimpleNamespace(command=None)

    class SyntheticHubScope:
        def task_snapshot(self, *, hub_task_id):
            return {'id': hub_task_id, 'tenant_id': state.command.tenant_id, 'project_id': 'synthetic-project',
                'worker_execution_context': {'native_node_command': state.command.to_dict()}}

        def revalidate(self, envelope):
            return envelope == state.command.authorization

    factory = Mock(return_value=store)
    handler = NativeResultMaintenanceHandler(scope=SyntheticHubScope(), store_factory=factory)
    original = queue.submit

    def delegated(command):
        state.command = command
        return original(command)

    queue.submit = delegated
    queue.runtime._handler = handler
    queue.runtime._capabilities = frozenset({MAINTENANCE_CAPABILITY})
    return SimpleNamespace(hub=hub, queue=queue, store=store, old_task=old_task,
        ledger=ledger, handler=handler, factory=factory, state=state)


def test_hub_delegated_cleanup_scrubs_payload_without_removing_replay_marker(cleanup):
    request = NativeGraphRequest(maintenance_plan(), 'synthetic-maintenance-run', 'synthetic-control',
        input_data={'batch_size': 100})
    result = cleanup.hub.start(request)
    if result.status != 'completed':
        result = cleanup.hub.advance(request)
    assert result.status == 'completed'
    assert len(cleanup.queue.submissions) == 1
    cleanup.factory.assert_called_once()
    with sqlite3.connect(cleanup.store.path) as db:
        assert db.execute('SELECT state, result_json, result_digest FROM native_results WHERE task_id=?',
                          (cleanup.old_task['id'],)).fetchone() == (
            'expired', None, None)
        assert db.execute("SELECT count(*) FROM native_results WHERE state='ready'").fetchone()[0] == 2
    with pytest.raises(ValueError, match='already_reserved'):
        cleanup.store.reserve(cleanup.old_task)


@pytest.mark.parametrize('batch', [0, 1001, True, '100', None])
def test_invalid_batch_is_denied_before_store_access(cleanup, batch):
    request = NativeGraphRequest(maintenance_plan(), 'synthetic-maintenance-run', 'synthetic-control',
        input_data={'batch_size': batch})
    result = cleanup.hub.start(request)
    if result.status not in {'completed', 'failed'}:
        result = cleanup.hub.advance(request)
    assert result.status == 'failed'
    cleanup.factory.assert_not_called()


def test_task_supplied_store_path_is_denied_before_store_access(cleanup):
    request = NativeGraphRequest(maintenance_plan(), 'synthetic-maintenance-run', 'synthetic-control',
        input_data={'batch_size': 1, 'path': '/foreign/results.sqlite3'})
    result = cleanup.hub.start(request)
    if result.status not in {'completed', 'failed'}:
        result = cleanup.hub.advance(request)
    assert result.status == 'failed'
    cleanup.factory.assert_not_called()


def test_expiry_only_handler_cannot_report_retirement_as_completed(cleanup):
    request = NativeGraphRequest(maintenance_plan(), 'synthetic-retirement-run', 'synthetic-control',
        input_data={'batch_size': 1, 'retire_expired': True, 'result_store_owner': {
            'worker_id': 'synthetic-worker', 'worker_url': 'http://synthetic-worker:8080',
            'store_binding_id': 'synthetic-store'}})
    result = cleanup.hub.start(request)
    if result.status not in {'completed', 'failed'}:
        result = cleanup.hub.advance(request)
    assert result.status == 'failed'
    cleanup.factory.assert_not_called()


def test_disabled_maintenance_never_falls_back_to_generic_handler():
    generic = Mock()
    router = build_result_maintenance_handlers(default=generic, scope=Mock(), native_config={})
    with pytest.raises(ValueError, match='maintenance_disabled'):
        router.execute(SimpleNamespace(node=SimpleNamespace(task_kind=MAINTENANCE_TASK_KIND)), hub_task_id='task')
    generic.execute.assert_not_called()


@pytest.mark.parametrize('enabled', [None, False, True])
def test_runtime_registration_advertises_maintenance_only_after_explicit_optin(enabled):
    native = {'enabled': True, 'allowed_task_types': [MAINTENANCE_TASK_KIND],
              'capabilities': [MAINTENANCE_CAPABILITY]}
    if enabled is not None:
        native['result_maintenance'] = enabled
    runtime = build_workflow_adapter_worker_runtime(
        agent_config={'worker_runtime': {'native_graph': native}}, client=Mock(),
        native_executor=Mock(), native_authorization_verifier=Mock())
    assert (MAINTENANCE_CAPABILITY in runtime.capabilities) is (enabled is True)
    assert 'workflow.adapter.native' in runtime.capabilities


@pytest.mark.parametrize('value', ['true', 1, {}, []])
def test_profile_rejects_nonboolean_optin(value):
    with pytest.raises(ValidationError):
        NativeGraphWorkerProfile.model_validate({'result_maintenance': value})


def test_profile_requires_explicit_task_and_capability_and_preserves_legacy_serialization():
    old = {'enabled': True, 'allowed_task_types': ['coding'], 'capabilities': ['tool_calling']}
    assert NativeGraphWorkerProfile.model_validate(old).model_dump(exclude_none=True) == old
    with pytest.raises(ValidationError, match='deployment_scope_required'):
        NativeGraphWorkerProfile.model_validate({**old, 'result_maintenance': True})
    config = {'enabled': True, 'allowed_task_types': [MAINTENANCE_TASK_KIND],
        'capabilities': [MAINTENANCE_CAPABILITY], 'result_maintenance': True}
    profile = NativeGraphWorkerProfile.model_validate(config)
    router = build_result_maintenance_handlers(default=UnavailableNativeNodeHandler('unsupported'),
        scope=Mock(), native_config=profile.model_dump(exclude_none=True))
    assert isinstance(router._handlers[MAINTENANCE_TASK_KIND], NativeResultMaintenanceHandler)


def test_unsigned_tool_scope_cannot_scrub_store(cleanup):
    request = NativeGraphRequest(maintenance_plan(), 'synthetic-maintenance-run', 'synthetic-control',
        input_data={'batch_size': 1})
    cleanup.hub.start(request)
    command = copy.deepcopy(cleanup.queue.submissions[0])
    command = replace(command, authorization=replace(command.authorization, allowed_tools=()))
    cleanup.factory.reset_mock()
    with pytest.raises(ValueError, match='scope_invalid'):
        cleanup.handler.execute(command, hub_task_id='hub-task-1')
    cleanup.factory.assert_not_called()


def test_signed_http_maintenance_does_not_trigger_global_housekeeping(cleanup):
    def queued(command):
        cleanup.queue.submissions.append(command)
        cleanup.state.command = command
        return HubTaskReceipt('maintenance-http-task', command.command_id, True)

    cleanup.queue.submit = queued
    request = NativeGraphRequest(maintenance_plan(), 'synthetic-http-maintenance-run', 'synthetic-control',
        input_data={'batch_size': 100})
    cleanup.hub.start(request)
    command = cleanup.queue.submissions[0]
    task = {'id': 'maintenance-http-task', 'tenant_id': command.tenant_id,
        'project_id': 'synthetic-project', 'task_kind': MAINTENANCE_TASK_KIND,
        'derivation_reason': 'native_graph_hub_delegation',
        'worker_execution_context': {'schema': 'ananta.native_graph_worker_context.v1',
            'runtime_path': 'native_graph_node', 'native_node_command': command.to_dict()}}
    signer = Ed25519SigningKeyRing({'synthetic': base64.b64encode(b's' * 32).decode()}, active_key_id='synthetic')
    verifier = Ed25519VerificationKeyRing.from_mapping(signer.verification_mapping())
    value = sign_native_execution_request(task, key_ring=signer, worker_id='synthetic-worker',
        worker_url='http://synthetic-worker:5000', expires_at=time.time() + 30)
    application = Flask(__name__)
    application.config.update(ROLE='worker', ANANTA_NATIVE_RESULT_STORE_PATH=str(cleanup.store.path))
    token = 'synthetic-maintenance-http-token-' + 'x' * 32

    def execute(task):
        result = cleanup.queue.runtime.execute(command, hub_task_id=task['id'])
        cleanup.queue.results[task['id']] = result
        return {'status': result.status, 'outcome': result.output_data}

    application.register_blueprint(create_native_execution_blueprint(token_provider=lambda: token,
        recipient_provider=lambda: ('synthetic-worker', 'http://synthetic-worker:5000'),
        verification_provider=lambda: verifier, consumer=execute,
        result_store_resolver=configured_native_result_store_for_task))
    client = application.test_client()
    response = client.post('/api/internal/native-tasks/execute', json=value,
        headers={'Authorization': 'Bearer ' + token})
    assert response.status_code == 200
    assert response.json == {'status': 'completed', 'outcome': {
        'scrubbed_results': 1, 'replay_tombstones_preserved': True}}
    assert cleanup.hub.advance(request).status == 'completed'
    retrieved = client.post('/api/internal/native-tasks/result', json=value,
        headers={'Authorization': 'Bearer ' + token})
    assert retrieved.json == response.json
    with sqlite3.connect(cleanup.store.path) as db:
        assert db.execute("SELECT count(*) FROM native_results WHERE state='ready'").fetchone()[0] == 3


def test_unknown_legacy_project_scope_is_not_guessed_by_delegated_cleanup(cleanup):
    with sqlite3.connect(cleanup.store.path) as db:
        db.execute('DELETE FROM native_result_scopes WHERE task_id=?', (cleanup.old_task['id'],))
    assert cleanup.store.expire_results(batch_size=100, tenant_id='tenant-a', project_id='synthetic-project') == 0
    # Explicit owner maintenance retains its existing global expiry contract.
    assert cleanup.store.expire_results(batch_size=100) == 3


@pytest.mark.parametrize('scope', [dict(tenant_id='tenant-a'), dict(project_id='synthetic-project'),
                                  dict(tenant_id='tenant-a', project_id=True)])
def test_partial_or_coercive_expiry_scope_is_rejected(cleanup, scope):
    with pytest.raises(ValueError, match='expiry_scope_invalid'):
        cleanup.store.expire_results(**scope)
