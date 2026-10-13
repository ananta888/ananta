"""Bounded owner-only capacity at full stores; synthetic execution identities."""

import copy
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest
from flask import Flask

from ananta_contracts.native_result_maintenance import MAINTENANCE_TASK_KIND
from ananta_contracts.native_task_execution import sign_native_execution_request
from tests.bpmn.test_native_result_retirement import retirement as retirement
from tests.bpmn.test_native_task_execution_http import signed as signed
from worker.runtime.native_graph.http_execution import create_native_execution_blueprint
from worker.runtime.native_graph.result_store import SQLiteNativeResultStore


@pytest.fixture
def capacity(tmp_path, signed):  # noqa: F811 -- imported fixture
    private = tmp_path / 'private'
    private.mkdir(mode=0o700)
    owner = {'worker_id': 'synthetic-worker', 'worker_url': 'http://synthetic-worker:8080',
             'store_binding_id': 'synthetic-store'}
    store = SQLiteNativeResultStore(private / 'results.sqlite3', max_records=1,
        max_maintenance_records=1, store_binding_id=owner['store_binding_id'], expire_on_open=False)
    normal = copy.deepcopy(signed[0]['task'])
    store.reserve(normal)
    maintenance = copy.deepcopy(normal)
    maintenance.update(id='maintenance-task', task_kind=MAINTENANCE_TASK_KIND)
    command = maintenance['worker_execution_context']['native_node_command']
    command['node']['task_kind'] = MAINTENANCE_TASK_KIND
    command['input_data'] = {'workflow_input': {'batch_size': 1, 'result_store_owner': owner}}
    return store, normal, maintenance, owner


def test_full_store_still_reserves_owner_maintenance_and_persists_its_result(capacity):
    store, normal, maintenance, owner = capacity
    store.reserve(maintenance, expire_before=False)
    store.publish(maintenance, {'status': 'completed', 'output': 'bounded-maintenance'}, expire_before=False)
    reopened = SQLiteNativeResultStore(store.path, max_records=1, max_maintenance_records=1,
        store_binding_id=owner['store_binding_id'], expire_on_open=False)
    assert reopened.read(maintenance, expire_before=False)['output'] == 'bounded-maintenance'
    with pytest.raises(ValueError, match='full|already_reserved'):
        reopened.reserve(maintenance, expire_before=False)
    with pytest.raises(ValueError, match='maintenance_full'):
        reopened.reserve({**maintenance, 'id': 'another-maintenance'}, expire_before=False)
    with pytest.raises(ValueError, match='store_full'):
        reopened.reserve({**normal, 'id': 'another-normal'}, expire_before=False)
    with sqlite3.connect(store.path) as db:
        assert db.execute('SELECT count(*) FROM native_results').fetchone()[0] == 2
        assert db.execute('SELECT task_id FROM native_result_maintenance_reservations').fetchone() == (
            maintenance['id'],)


@pytest.mark.parametrize('case', ['normal', 'wrong_owner', 'unbound_task', 'unbound_store', 'no_project'])
def test_extra_capacity_is_unavailable_to_foreign_or_unbound_requests(capacity, case):
    store, normal, maintenance, _ = capacity
    task = copy.deepcopy(maintenance)
    if case == 'normal':
        task = {**normal, 'id': 'another-normal'}
    elif case == 'wrong_owner':
        task['worker_execution_context']['native_node_command']['input_data']['workflow_input'][
            'result_store_owner']['store_binding_id'] = 'foreign-store'
    elif case == 'unbound_task':
        del task['worker_execution_context']['native_node_command']['input_data']['workflow_input'][
            'result_store_owner']
    elif case == 'unbound_store':
        store = SQLiteNativeResultStore(store.path, max_records=1)
    else:
        task['project_id'] = None
    with pytest.raises(ValueError, match='store_full'):
        store.reserve(task, expire_before=False)
    with sqlite3.connect(store.path) as db:
        assert db.execute('SELECT count(*) FROM native_results').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM native_result_maintenance_reservations').fetchone()[0] == 0


def test_parallel_reservations_cannot_overrun_maintenance_capacity(capacity):
    store, _, maintenance, owner = capacity

    def reserve(index):
        instance = SQLiteNativeResultStore(store.path, max_records=1, max_maintenance_records=1,
            store_binding_id=owner['store_binding_id'], expire_on_open=False)
        try:
            instance.reserve({**maintenance, 'id': 'maintenance-' + str(index)}, expire_before=False)
            return 'reserved'
        except ValueError:
            return 'denied'

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(reserve, range(8)))
    assert outcomes.count('reserved') == 1 and outcomes.count('denied') == 7


def test_existing_maintenance_uses_regular_capacity_before_emergency_reserve(capacity):
    original, normal, maintenance, owner = capacity
    store = SQLiteNativeResultStore(original.path, max_records=2, max_maintenance_records=0,
        store_binding_id=owner['store_binding_id'], expire_on_open=False)
    store.reserve(maintenance, expire_before=False)
    with sqlite3.connect(store.path) as db:
        assert db.execute('SELECT count(*) FROM native_result_maintenance_reservations').fetchone()[0] == 0
    with pytest.raises(ValueError, match='maintenance_full'):
        store.reserve({**maintenance, 'id': 'another-maintenance'}, expire_before=False)
    with pytest.raises(ValueError, match='store_full'):
        store.reserve({**normal, 'id': 'another-normal'}, expire_before=False)


def test_expiry_does_not_reopen_extra_capacity_or_remove_replay_protection(capacity):
    original, _, maintenance, owner = capacity
    clock = [100]
    store = SQLiteNativeResultStore(original.path, clock=lambda: clock[0], retention_seconds=1,
        max_records=1, max_maintenance_records=1, store_binding_id=owner['store_binding_id'], expire_on_open=False)
    store.reserve(maintenance, expire_before=False)
    clock[0] = 101
    assert store.expire_results(tenant_id=maintenance['tenant_id'], project_id=maintenance['project_id']) == 1
    with pytest.raises(ValueError, match='maintenance_full|already_reserved'):
        store.reserve(maintenance, expire_before=False)
    with pytest.raises(ValueError, match='maintenance_full'):
        store.reserve({**maintenance, 'id': 'another-maintenance'}, expire_before=False)


def test_mismatched_capacity_metadata_never_increases_available_capacity(capacity):
    store, _, maintenance, _ = capacity
    store.reserve(maintenance, expire_before=False)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE native_result_maintenance_reservations SET binding_digest='foreign'")
    with pytest.raises(ValueError, match='maintenance_full'):
        store.reserve({**maintenance, 'id': 'another-maintenance'}, expire_before=False)


def test_hub_permit_compaction_recycles_emergency_capacity_but_keeps_replay_denial(retirement):  # noqa: F811
    original, clock, normal, current, owner, payload, verifier, sign = retirement
    store = SQLiteNativeResultStore(original.path, clock=lambda: clock[0], retention_seconds=1,
        max_records=1, max_maintenance_records=1, store_binding_id=owner['store_binding_id'], expire_on_open=False)
    old_maintenance = {**current, 'id': 'old-maintenance'}
    store.reserve(old_maintenance, expire_before=False)
    clock[0] = 101
    permit = copy.deepcopy(payload)
    permit['entries'].append({'task_id': old_maintenance['id'], 'binding_digest': store._binding(old_maintenance)[1]})
    assert store.compact_results(sign(permit), task=current, owner=owner, key_ring=verifier) == 2
    with sqlite3.connect(store.path) as db:
        assert db.execute('SELECT count(*) FROM native_result_maintenance_reservations').fetchone()[0] == 0
    for task in (normal, old_maintenance):
        with pytest.raises(ValueError, match='already_reserved'):
            store.reserve(task, expire_before=False)
    store.reserve({**normal, 'id': 'new-normal'}, expire_before=False)
    store.reserve({**current, 'id': 'new-maintenance'}, expire_before=False)


def test_durable_store_identity_refuses_relabeling_after_restart(capacity):
    store, _, _, owner = capacity
    SQLiteNativeResultStore(store.path, store_binding_id=owner['store_binding_id'])
    with pytest.raises(ValueError, match='binding_changed'):
        SQLiteNativeResultStore(store.path, store_binding_id='replacement-store')
    with sqlite3.connect(store.path) as db:
        binding, = db.execute('SELECT binding_id FROM native_result_store_identity').fetchone()
        assert binding == owner['store_binding_id']
        assert db.execute('SELECT count(*) FROM native_results').fetchone()[0] == 1


def test_concurrent_first_binding_has_one_durable_owner(capacity):
    existing, *_ = capacity
    path = existing.path.parent / 'unbound.sqlite3'
    SQLiteNativeResultStore(path, expire_on_open=False)

    def bind(identity):
        try:
            SQLiteNativeResultStore(path, store_binding_id=identity, expire_on_open=False)
            return identity
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(bind, ['owner-a', 'owner-b']))
    accepted = [value for value in results if value is not None]
    assert len(accepted) == 1
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT binding_id FROM native_result_store_identity').fetchone()[0] == accepted[0]


@pytest.mark.parametrize('outcome,expected', [(False, 409), ('truthy', 409), (None, 409), ('unavailable', 503)])
def test_denied_current_authority_cannot_consume_maintenance_capacity(capacity, signed, outcome, expected):  # noqa: F811
    import base64

    from ananta_contracts.runtime_authorization_crypto import Ed25519SigningKeyRing

    store, _, task, owner = capacity
    admission = Mock(side_effect=RuntimeError('synthetic-unavailable')) if outcome == 'unavailable' else Mock(
        return_value=outcome)
    provider, consumer = Mock(return_value=store), Mock(return_value={'status': 'completed'})
    app = Flask(__name__)
    app.config['ROLE'] = 'worker'
    app.register_blueprint(create_native_execution_blueprint(token_provider=lambda: 'x' * 40,
        recipient_provider=lambda: (owner['worker_id'], owner['worker_url']),
        verification_provider=lambda: signed[1], result_store_provider=provider, consumer=consumer,
        execution_admission=admission))
    signer = Ed25519SigningKeyRing({'synthetic': base64.b64encode(b's' * 32).decode()}, active_key_id='synthetic')
    value = sign_native_execution_request(task, key_ring=signer, worker_id=owner['worker_id'],
        worker_url=owner['worker_url'], expires_at=time.time() + 30)
    response = app.test_client().post('/api/internal/native-tasks/execute', json=value,
                                    headers={'Authorization': 'Bearer ' + 'x' * 40})
    assert response.status_code == expected
    admission.assert_called_once()
    provider.assert_not_called()
    consumer.assert_not_called()
    with sqlite3.connect(store.path) as db:
        assert db.execute('SELECT count(*) FROM native_result_maintenance_reservations').fetchone()[0] == 0


def test_productive_default_blueprint_uses_current_runtime_admission_before_store(capacity, signed):  # noqa: F811
    import base64

    from ananta_contracts.runtime_authorization_crypto import Ed25519SigningKeyRing
    from worker.runtime.workflow_adapter_runtime_composition import WorkflowAdapterWorkerRuntime

    store, _, task, owner = capacity
    admission, provider, consumer = Mock(), Mock(return_value=store), Mock()
    admission.authorize.return_value = False
    app = Flask(__name__)
    app.config['ROLE'] = 'worker'
    app.extensions['workflow_adapter_worker_runtime'] = WorkflowAdapterWorkerRuntime(
        consumer=consumer, native_admission=admission)
    app.register_blueprint(create_native_execution_blueprint(token_provider=lambda: 'x' * 40,
        recipient_provider=lambda: (owner['worker_id'], owner['worker_url']),
        verification_provider=lambda: signed[1], result_store_provider=provider))
    signer = Ed25519SigningKeyRing({'synthetic': base64.b64encode(b's' * 32).decode()}, active_key_id='synthetic')
    payload = sign_native_execution_request(task, key_ring=signer, worker_id=owner['worker_id'],
        worker_url=owner['worker_url'], expires_at=time.time() + 30)
    response = app.test_client().post('/api/internal/native-tasks/execute', json=payload,
                                    headers={'Authorization': 'Bearer ' + 'x' * 40})
    assert response.status_code == 409
    admission.authorize.assert_called_once_with(task)
    provider.assert_not_called()
    consumer.consume.assert_not_called()
