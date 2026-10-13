"""Synthetic signed compaction permits; no production evidence or policy bypass."""

import base64
import copy
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from ananta_contracts.native_context_bundle import native_context_digest
from ananta_contracts.native_result_binding import native_result_binding
from ananta_contracts.native_result_retirement import (
    RETIREMENT_CAPABILITY,
    RETIREMENT_SCHEMA,
    RETIREMENT_TOOL,
)
from ananta_contracts.runtime_authorization_crypto import Ed25519SigningKeyRing, Ed25519VerificationKeyRing
from tests.bpmn.test_native_task_execution_http import signed as signed
from worker.runtime.native_graph.result_store import SQLiteNativeResultStore


@pytest.fixture
def retirement(tmp_path, signed):  # noqa: F811 -- imported pytest fixture
    clock = [100]
    directory = tmp_path / 'private'
    directory.mkdir(mode=0o700)
    store = SQLiteNativeResultStore(directory / 'results.sqlite3', clock=lambda: clock[0],
        retention_seconds=1, max_records=1, expire_on_open=False)
    old = copy.deepcopy(signed[0]['task'])
    store.reserve(old)
    store.publish(old, {'status': 'completed', 'output': 'synthetic-private-payload'})
    owner = {'worker_id': 'synthetic-worker', 'worker_url': 'http://synthetic-worker:8080',
             'store_binding_id': 'synthetic-private-store'}
    task = copy.deepcopy(old)
    task.update(id='maintenance-task', task_kind='native_result_maintenance')
    command = task['worker_execution_context']['native_node_command']
    command.update(command_id='maintenance-command',
        authorization={'expires_at': 130, 'allowed_tools': ['native.result.expire', RETIREMENT_TOOL]},
        input_data={'workflow_input': {'batch_size': 2, 'result_store_owner': owner, 'retire_expired': True}},
        node={'task_kind': task['task_kind'], 'required_capabilities': [RETIREMENT_CAPABILITY],
              'allowed_tools': ['native.result.expire', RETIREMENT_TOOL]})
    signer = Ed25519SigningKeyRing({'synthetic': base64.b64encode(b'r' * 32).decode()}, active_key_id='synthetic')
    verifier = Ed25519VerificationKeyRing.from_mapping(signer.verification_mapping())
    payload = {'schema': RETIREMENT_SCHEMA, 'tenant_id': old['tenant_id'], 'project_id': old['project_id'],
        'maintenance_task_id': task['id'], 'command_digest': native_context_digest(command),
        'owner': owner, 'expires_at': 125,
        'entries': [{'task_id': old['id'], 'binding_digest': native_result_binding(old)[1]}]}

    def sign(value):
        key_id, signature = signer.sign(namespace=RETIREMENT_SCHEMA, payload=value)
        return {**copy.deepcopy(value), 'key_id': key_id, 'signature': signature}

    return store, clock, old, task, owner, payload, verifier, sign


def compact(fixture, *, payload=None, task=None, owner=None):
    store, _, _, current, registered, original, verifier, sign = fixture
    return store.compact_results(sign(original if payload is None else payload),
        task=current if task is None else task, owner=registered if owner is None else owner, key_ring=verifier)


def test_compaction_frees_capacity_but_denies_replay_after_restart(retirement):
    store, clock, old, *_ = retirement
    assert compact(retirement) == 0  # Unexpired results remain retrievable.
    assert store.read(old)['output'] == 'synthetic-private-payload'
    clock[0] = 101
    assert compact(retirement) == 1
    assert compact(retirement) == 0
    with sqlite3.connect(store.path) as db:
        assert db.execute('SELECT count(*) FROM native_results').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM native_result_scopes').fetchone()[0] == 0
        row = db.execute('SELECT * FROM native_result_tombstones').fetchone()
        assert row[:3] == (old['tenant_id'], old['id'], native_result_binding(old)[1])
        assert 'synthetic-private-payload' not in repr(row)
    reopened = SQLiteNativeResultStore(store.path, clock=lambda: clock[0], max_records=1)
    for modified in (old, {**old, 'project_id': 'different-project'}, {**old, 'input_files': ['changed']}):
        with pytest.raises(ValueError, match='already_reserved'):
            reopened.reserve(modified)
        with pytest.raises(ValueError):
            reopened.read(modified)
        with pytest.raises(ValueError, match='publication_denied'):
            reopened.publish(modified, {'status': 'completed'})
    next_task = {**old, 'id': 'next-task'}
    reopened.reserve(next_task)
    reopened.publish(next_task, {'status': 'completed'})
    assert reopened.read(next_task)['status'] == 'completed'


@pytest.mark.parametrize('field,value', [
    ('tenant_id', 'foreign'), ('project_id', 'foreign'), ('maintenance_task_id', 'foreign'),
    ('command_digest', '0' * 64), ('schema', 'foreign'), ('expires_at', 100),
    ('expires_at', 131), ('expires_at', True), ('expires_at', float('nan')),
    ('entries', [{'task_id': 'maintenance-task', 'binding_digest': '0' * 64}]),
    ('entries', [{'task_id': 'old', 'binding_digest': 'malformed'}]),
    ('extra', 'not-allowed'),
])
def test_even_signed_foreign_or_malformed_permit_cannot_change_storage(retirement, field, value):
    store, clock, *_ = retirement
    clock[0] = 101
    payload = {**retirement[5], field: value}
    with pytest.raises(ValueError):
        compact(retirement, payload=payload)
    with sqlite3.connect(store.path) as db:
        assert db.execute('SELECT count(*) FROM native_results').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM native_result_tombstones').fetchone()[0] == 0


def test_unsigned_tampered_or_wrong_recipient_permit_denied(retirement):
    store, clock, _, task, owner, payload, verifier, sign = retirement
    clock[0] = 101
    for manifest in (payload, {**sign(payload), 'signature': 'forged'},
                     {**sign(payload), 'entries': []}):
        with pytest.raises(ValueError):
            store.compact_results(manifest, task=task, owner=owner, key_ring=verifier)
    with pytest.raises(ValueError):
        compact(retirement, owner={**owner, 'store_binding_id': 'different-store'})
    with pytest.raises(ValueError):
        store.compact_results(sign(payload), task=task, owner=owner, key_ring=None)


def test_foreign_scope_and_binding_are_never_compacted(retirement):
    store, clock, old, *_ = retirement
    clock[0] = 101
    payload = copy.deepcopy(retirement[5])
    payload['entries'][0]['binding_digest'] = '0' * 64
    assert compact(retirement, payload=payload) == 0
    with sqlite3.connect(store.path) as db:
        db.execute('UPDATE native_result_scopes SET project_id=?', ('foreign-project',))
    assert compact(retirement) == 0
    with pytest.raises(ValueError, match='full|already_reserved'):
        store.reserve(old)


def test_tombstone_capacity_failure_rolls_back_entire_batch(retirement):
    original, clock, old, *_ = retirement
    clock[0] = 101
    store = SQLiteNativeResultStore(original.path, clock=lambda: clock[0], max_tombstones=1,
                                   expire_on_open=False)
    with sqlite3.connect(store.path) as db:
        db.execute('INSERT INTO native_result_tombstones VALUES (?, ?, ?, ?)',
                   ('another-tenant', 'occupied', 'a' * 64, 'b' * 64))
    with pytest.raises(ValueError, match='tombstones_full'):
        store.compact_results(retirement[7](retirement[5]), task=retirement[3], owner=retirement[4],
                              key_ring=retirement[6])
    with sqlite3.connect(store.path) as db:
        assert db.execute('SELECT result_json FROM native_results').fetchone()[0] is not None
        assert db.execute('SELECT task_id FROM native_result_tombstones').fetchone()[0] == 'occupied'
    with pytest.raises(ValueError, match='full|already_reserved'):
        original.reserve(old)


def test_concurrent_compaction_and_replay_never_reopens_execution(retirement):
    store, clock, old, *_ = retirement
    clock[0] = 101

    def attempt(index):
        if index % 2:
            try:
                store.reserve(old, expire_before=False)
            except ValueError:
                return 'denied'
            return 'reexecuted'
        return compact(retirement)

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(attempt, range(12)))
    assert outcomes.count(1) == 1
    assert outcomes.count('denied') == 6
    assert 'reexecuted' not in outcomes


def test_permit_expiring_during_write_lock_wait_cannot_compact(retirement, monkeypatch):
    store, clock, *_ = retirement
    clock[0] = 101
    connect = store._connect

    @contextmanager
    def delayed_connect():
        with connect() as db:
            clock[0] = 125
            yield db

    monkeypatch.setattr(store, '_connect', delayed_connect)
    with pytest.raises(ValueError, match='expired'):
        compact(retirement)
    with sqlite3.connect(store.path) as db:
        assert db.execute('SELECT count(*) FROM native_results').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM native_result_tombstones').fetchone()[0] == 0


@pytest.mark.parametrize('case', ['duplicate', 'over_batch', 'extra_entry_field'])
def test_manifest_entry_set_is_closed_and_bounded(retirement, case):
    payload = copy.deepcopy(retirement[5])
    if case == 'duplicate':
        payload['entries'] *= 2
    elif case == 'over_batch':
        payload['entries'] = [{'task_id': 'slot-' + str(index), 'binding_digest': 'a' * 64}
                              for index in range(3)]
    else:
        payload['entries'][0]['result_json'] = 'must-never-be-admitted'
    with pytest.raises(ValueError, match='entries_invalid'):
        compact(retirement, payload=payload)


def test_uncomposed_retirement_is_not_advertised_as_worker_capability():
    from worker.runtime.workflow_adapter_runtime_composition import _native_worker_capabilities

    config = {'worker_runtime': {'native_graph': {'result_maintenance': True,
        'capabilities': ['workflow.result_maintenance', RETIREMENT_CAPABILITY]}}}
    assert _native_worker_capabilities(config) == ('workflow.result_maintenance',)


def test_shared_binding_preserves_legacy_ascii_json_slots(retirement):
    store, _, old, *_ = retirement
    task = {**old, 'input_files': ['déjà-vu.py']}
    # Frozen digest from the original private-store ASCII JSON format. A switch
    # to native_context_digest's Unicode format would strand existing results.
    legacy = '865e7a024264a0481f0b055b1007d469dd93e397d48efd62a05416897a544b51'
    with sqlite3.connect(store.path) as db:
        db.execute('UPDATE native_results SET binding_digest=?', (legacy,))
        db.execute('UPDATE native_result_scopes SET binding_digest=?', (legacy,))
    assert native_result_binding(task)[1] == legacy
    assert store.read(task, expire_before=False)['output'] == 'synthetic-private-payload'
