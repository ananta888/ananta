"""Actual SQL snapshots, audit transactions, Hub write hooks and operator ACLs."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace

import pytest
from flask import Flask, g
from sqlalchemy import event, select
from sqlmodel import Session

from agent.db_models import MemoryEntryDB, ProjectMembershipDB
from agent.db_models.memory_audit import MemoryAuditEventDB
from agent.db_models.memory_snapshot import MemorySnapshotDB
from agent.repositories.memory_audit import SQLMemoryAuditRepository
from agent.repositories.memory_snapshot import SQLMemorySnapshotRepository
from agent.routes.memory_audit import memory_audit_bp
from agent.services.memory_audit_read_service import MemoryAuditQuery, MemoryAuditReadService
from agent.services.memory_diff_service import compare_memory_snapshots
from agent.services.memory_runtime import HubMemoryRuntime
from agent.services.memory_snapshot_service import MemorySnapshotService
from agent.services.project_access_authority import SqlProjectAccessAuthority
from agent.services.result_memory_service import ResultMemoryService
from agent.services.source_control_access_policy import HubSourcePrincipal
from ananta_contracts.memory_access import MemorySecurityMetadata
from tests.test_memory_policy_repository import persisted as persisted  # noqa: F401


@pytest.fixture
def snapshots(persisted, monkeypatch):  # noqa: F811 - imported pytest fixture
    engine, _, policy = persisted
    repository = SQLMemorySnapshotRepository(engine, clock=lambda: 100)
    monkeypatch.setattr('agent.repositories.memory._engine', lambda: engine)
    with Session(engine) as session, session.begin():
        session.add(ProjectMembershipDB(tenant_id=policy.actor.tenant_id,
            project_id=policy.namespace.workspace_id, subject_id='operator', role='owner'))
    return engine, policy, repository, MemorySnapshotService(repository=repository)


def entry(snapshots, *, identity='entry-a', namespace=None):
    engine, policy, _, _ = snapshots
    metadata = {'memory_security_schema': 'ananta.memory-security.v1',
        'memory_namespace': asdict(namespace or policy.namespace),
        'memory_security': MemorySecurityMetadata().to_mapping(), 'memory_policy_digest': policy.digest}
    with Session(engine) as session, session.begin():
        session.add(MemoryEntryDB(id=identity, task_id=policy.namespace.task_id, goal_id=policy.namespace.goal_id,
            title='Synthetic result', content='synthetic-private-content-canary', memory_metadata=metadata))


def test_actual_sql_before_after_diff_is_content_free_and_audit_bound(snapshots):
    engine, policy, repository, service = snapshots
    before = service.capture(policy, 'before')
    entry(snapshots)
    after = service.capture(policy, 'after', scope_outcome='completed')
    diff = compare_memory_snapshots(before, after)
    assert diff['status'] == 'complete' and diff['changes'][0]['kind'] == 'added'
    assert 'synthetic-private-content-canary' not in json.dumps([before, after, diff])
    assert len(diff['audit_event_ids']) == 2 and diff['attribution'] == 'observed_scope_change'
    assert SQLMemoryAuditRepository(engine).verify(tenant_id=policy.actor.tenant_id)['verified']
    query = MemoryAuditQuery(run_id=policy.namespace.run_id)
    assert len(repository.read_run(tenant_id=policy.actor.tenant_id,
        project_id=policy.namespace.workspace_id, query=query)) == 2


@pytest.mark.parametrize('mutation,expected', [('unchanged', []), ('modified', ['modified']), ('deleted', ['deleted'])])
def test_unchanged_content_is_not_reported_as_a_change(snapshots, mutation, expected):
    engine, policy, _, service = snapshots
    entry(snapshots)
    before = service.capture(policy, 'before')
    with Session(engine) as session, session.begin():
        row = session.get(MemoryEntryDB, 'entry-a')
        if mutation == 'modified':
            row.content = 'synthetic-new-content'
        elif mutation == 'deleted':
            session.delete(row)
    after = service.capture(policy, 'after', scope_outcome='completed')
    assert [item['kind'] for item in compare_memory_snapshots(before, after)['changes']] == expected


@pytest.mark.parametrize('field', ['tenant_id', 'workspace_id', 'task_id', 'goal_id'])
def test_forged_source_namespace_cannot_enter_the_snapshot(snapshots, field):
    _, policy, _, service = snapshots
    entry(snapshots, namespace=replace(policy.namespace, **{field: 'foreign'}))
    assert service.capture(policy, 'before')['entries'] == []


def test_entries_above_the_run_sensitivity_ceiling_are_not_hydrated(snapshots):
    engine, policy, _, service = snapshots
    entry(snapshots)
    with Session(engine) as session, session.begin():
        row = session.get(MemoryEntryDB, 'entry-a')
        metadata = dict(row.memory_metadata)
        metadata['memory_security'] = {**metadata['memory_security'], 'sensitivity': 'secret'}
        row.memory_metadata = metadata
    assert service.capture(policy, 'before')['entries'] == []


def test_before_boundary_is_immutable_and_concurrent_capture_is_idempotent(snapshots):
    engine, policy, repository, service = snapshots
    with ThreadPoolExecutor(max_workers=2) as pool:
        calls = [pool.submit(service.capture, policy, 'before') for _ in range(2)]
        snapshots_written = [call.result(timeout=10) for call in calls]
    assert snapshots_written[0] == snapshots_written[1]
    entry(snapshots)
    assert service.capture(policy, 'before') == snapshots_written[0]
    with Session(engine) as session:
        assert len(session.execute(select(MemorySnapshotDB)).scalars().all()) == 1
    query = MemoryAuditQuery(run_id=policy.namespace.run_id)
    records = repository.read_run(tenant_id=policy.actor.tenant_id,
                                  project_id=policy.namespace.workspace_id, query=query)
    assert compare_memory_snapshots(records[0], None)['status'] == 'incomplete'


def test_snapshot_and_its_audit_event_rollback_together(snapshots):
    engine, policy, _, service = snapshots

    def fail(_mapper, _connection, _target):
        raise RuntimeError('synthetic snapshot insert failure')

    event.listen(MemorySnapshotDB, 'before_insert', fail)
    try:
        with pytest.raises(RuntimeError, match='synthetic snapshot insert failure'):
            service.capture(policy, 'before')
    finally:
        event.remove(MemorySnapshotDB, 'before_insert', fail)
    with Session(engine) as session:
        assert session.execute(select(MemorySnapshotDB)).scalars().all() == []
        assert session.execute(select(MemoryAuditEventDB)).scalars().all() == []


def test_tampered_snapshot_or_missing_audit_link_is_not_a_diff(snapshots):
    engine, policy, repository, service = snapshots
    before = service.capture(policy, 'before')
    with Session(engine) as session, session.begin():
        row = session.get(MemorySnapshotDB, before['snapshot_id'])
        row.snapshot = {**row.snapshot, 'entries': [{'entry_id': 'forged'}]}
    with pytest.raises(ValueError, match='integrity_invalid'):
        repository.read_run(tenant_id=policy.actor.tenant_id, project_id=policy.namespace.workspace_id,
                            query=MemoryAuditQuery(run_id=policy.namespace.run_id))


def test_deleted_audit_link_prevents_snapshot_reuse(snapshots):
    engine, policy, repository, service = snapshots
    before = service.capture(policy, 'before')
    with Session(engine) as session, session.begin():
        session.delete(session.get(MemoryAuditEventDB, before['audit_event_id']))
    with pytest.raises(ValueError, match='audit_chain_invalid'):
        repository.read_run(tenant_id=policy.actor.tenant_id, project_id=policy.namespace.workspace_id,
                            query=MemoryAuditQuery(run_id=policy.namespace.run_id))


def test_snapshot_input_limit_blocks_capture_before_content_hydration(snapshots, monkeypatch):
    engine, policy, _, service = snapshots
    entry(snapshots)
    monkeypatch.setattr('agent.repositories.result_memory_snapshot_source.MAX_SNAPSHOT_INPUT_CHARS', 8)
    with pytest.raises(ValueError, match='observation_limit'):
        service.capture(policy, 'before')
    with Session(engine) as session:
        assert session.execute(select(MemorySnapshotDB)).scalars().all() == []
        assert session.execute(select(MemoryAuditEventDB)).scalars().all() == []


@pytest.mark.parametrize('unsafe', ['content', 'sensitivity'])
def test_unsafe_source_adapter_cannot_persist_content_or_broaden_sensitivity(snapshots, unsafe):
    engine, policy, _, _ = snapshots

    class UnsafeSource:
        def observe(self, session, namespace, *, sensitivity_ceiling):
            item = {'entry_id': 'unsafe-entry', 'content_digest': 'a' * 64, 'metadata_digest': 'b' * 64,
                    'security': MemorySecurityMetadata().to_mapping()}
            if unsafe == 'content':
                item['raw_content'] = 'synthetic-private-canary'
            else:
                item['security']['sensitivity'] = 'secret'
            return [item]

    repository = SQLMemorySnapshotRepository(engine, clock=lambda: 100, source=UnsafeSource())
    with pytest.raises(ValueError, match='source_invalid'):
        repository.capture(policy=policy, phase='before', scope_outcome=None)
    with Session(engine) as session:
        assert session.execute(select(MemorySnapshotDB)).scalars().all() == []


def test_readonly_or_unregistered_run_cannot_create_a_snapshot(snapshots):
    _, policy, repository, service = snapshots
    with pytest.raises(ValueError, match='channel_unsupported'):
        service.capture(replace(policy, capabilities=()), 'before')
    with pytest.raises(ValueError, match='admission_invalid'):
        repository.capture(policy=replace(policy, namespace=replace(policy.namespace, run_id='unregistered')),
                           phase='before', scope_outcome=None)


@pytest.mark.parametrize('failed', [False, True])
def test_productive_hub_write_session_captures_actual_result_memory_even_when_scope_fails(snapshots, failed):
    engine, policy, repository, _ = snapshots
    runtime = HubMemoryRuntime(engine, clock=lambda: 100)
    app = Flask(__name__)
    app.extensions['hub_memory_runtime'] = runtime
    run_id = None
    with app.test_request_context('/synthetic-result'):
        g.service_identity = {'worker_id': policy.actor.subject}
        try:
            with runtime.worker_result(policy.namespace.task_id, 'synthetic-job') as context:
                run_id = context.run_id
                ResultMemoryService().record_worker_result_memory(
                    task_id=policy.namespace.task_id, goal_id=policy.namespace.goal_id, trace_id=None,
                    worker_job_id='synthetic-job', title='Synthetic result', output='synthetic completed work',
                    policy={'create_followup_artifact': False})
                if failed:
                    raise RuntimeError('synthetic scope failure')
        except RuntimeError:
            assert failed
    records = repository.read_run(tenant_id=policy.actor.tenant_id, project_id=policy.namespace.workspace_id,
                                  query=MemoryAuditQuery(run_id=run_id))
    pair = {item['phase']: item for item in records}
    diff = compare_memory_snapshots(pair['before'], pair['after'])
    assert diff['changes'][0]['kind'] == 'added'
    assert diff['scope_outcome'] == ('failed' if failed else 'completed')
    assert runtime.policies.current(actor=policy.actor, run_id=run_id) is None


def test_operator_api_reads_the_same_verified_diff_and_denies_another_project(snapshots, monkeypatch):
    engine, policy, repository, capture = snapshots
    capture.capture(policy, 'before')
    entry(snapshots)
    capture.capture(policy, 'after', scope_outcome='completed')
    service = MemoryAuditReadService(access=SqlProjectAccessAuthority(session_factory=lambda: Session(engine)),
        repository=SQLMemoryAuditRepository(engine), snapshots=repository)
    principal = HubSourcePrincipal('operator', policy.actor.tenant_id, None, frozenset())
    app = Flask(__name__)
    app.config['ROLE'] = 'hub'
    app.extensions['memory_audit_read_service'] = service
    app.register_blueprint(memory_audit_bp)

    def authenticate(token, *, require_admin):
        if token != 'synthetic-token':
            return False, 'missing_token'
        g.user = {'sub': 'operator', 'tenant_id': policy.actor.tenant_id}
        g.is_admin = False
        return True, 'synthetic_fixture'

    monkeypatch.setattr('agent.auth._authenticate_request', authenticate)
    path = '/api/projects/' + policy.namespace.workspace_id + '/memory-audit/diffs?run_id=' + policy.namespace.run_id
    response = app.test_client().get(path, headers={'Authorization': 'Bearer synthetic-token'})
    assert response.status_code == 200
    assert response.json['data'] == service.diffs(principal=principal, project_id=policy.namespace.workspace_id,
                                                query=MemoryAuditQuery(run_id=policy.namespace.run_id))
    assert app.test_client().get(path.replace(policy.namespace.workspace_id, 'foreign'),
        headers={'Authorization': 'Bearer synthetic-token'}).status_code == 404
