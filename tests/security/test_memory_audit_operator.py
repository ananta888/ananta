"""Real SQL audit/project authorization with deterministic HTTP auth fixtures."""

from dataclasses import replace

import pytest
from flask import Flask, g
from sqlalchemy import update
from sqlmodel import Session, SQLModel, create_engine

from agent.db_models.memory_audit import MemoryAuditEventDB
from agent.db_models.projects import ProjectDB, ProjectMembershipDB
from agent.repositories.memory_audit import SQLMemoryAuditRepository
from agent.routes.memory_audit import memory_audit_bp
from agent.services.memory_audit_read_service import MemoryAuditQuery, MemoryAuditReadService
from agent.services.memory_audit_service import MemoryAuditService
from agent.services.memory_policy_gateway import MemoryPolicyDecision
from agent.services.project_access_authority import SqlProjectAccessAuthority
from agent.services.source_control_access_policy import HubSourcePrincipal
from ananta_contracts.memory_access import (
    MemoryActor,
    MemoryNamespace,
    MemoryOperation,
    MemoryScope,
    MemorySecurityMetadata,
    MemoryTarget,
)


@pytest.fixture
def operator(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'operator.sqlite3'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session, session.begin():
        for tenant, project in [('tenant-a', 'project-a'), ('tenant-a', 'project-b'), ('tenant-b', 'project-a')]:
            session.add(ProjectDB(tenant_id=tenant, project_id=project, name='Synthetic project',
                                  created_by_subject_id='operator'))
        session.add(ProjectMembershipDB(tenant_id='tenant-a', project_id='project-a',
                                        subject_id='operator', role='owner'))
        session.add(ProjectMembershipDB(tenant_id='tenant-a', project_id='project-a',
                                        subject_id='viewer', role='viewer'))
    repository = SQLMemoryAuditRepository(engine)
    audit = MemoryAuditService(repository=repository, retention_seconds=3600, clock=lambda: 100)
    for tenant, project, run, channel in [
        ('tenant-a', 'project-a', 'run-1', 'run-scratchpad'),
        ('tenant-a', 'project-b', 'run-1', 'run-scratchpad'),
        ('tenant-b', 'project-a', 'run-1', 'run-scratchpad'),
        ('tenant-a', 'project-a', 'run-2', 'hub-result-memory'),
        ('tenant-a', 'project-a', 'run-1', 'run-scratchpad'),
    ]:
        audit.record(MemoryPolicyDecision(allowed=True, reason_code='memory_access_allowed',
            rule_id='memory.explicit-capability.v1', actor=MemoryActor(tenant, 'worker'), run_id=run,
            operation=MemoryOperation.read, target=MemoryTarget(channel,
                MemoryNamespace(tenant, project, 'goal', 'task-' + run, run),
                MemorySecurityMetadata(scope=MemoryScope.run)),
            policy_revision='synthetic-policy', policy_digest='a' * 64, expires_at=900, trace_id='trace-' + run))
    service = MemoryAuditReadService(access=SqlProjectAccessAuthority(session_factory=lambda: Session(engine)),
                                   repository=repository)
    principal = HubSourcePrincipal('operator', 'tenant-a', None, frozenset())
    app = Flask(__name__)
    app.config['ROLE'] = 'hub'
    app.extensions['memory_audit_read_service'] = service
    app.register_blueprint(memory_audit_bp)

    def authenticate(token, *, require_admin):
        if token != 'synthetic-operator-token':
            return False, 'missing_token'
        g.user = {'sub': principal.subject_id, 'tenant_id': principal.tenant_id}
        g.is_admin = False
        return True, 'synthetic_fixture'

    monkeypatch.setattr('agent.auth._authenticate_request', authenticate)
    yield engine, repository, service, principal, app
    engine.dispose()


def page(operator, **values):
    _, _, service, principal, _ = operator
    return service.page(principal=principal, project_id='project-a', query=MemoryAuditQuery(**values))


def test_sql_scope_filtering_precedes_page_limit_and_cursor(operator):
    first = page(operator, page_size=1)
    assert [row['sequence'] for row in first['items']] == [1]
    second = page(operator, page_size=1, after_sequence=first['next_after_sequence'])
    assert [row['sequence'] for row in second['items']] == [3]
    final = page(operator, page_size=1, after_sequence=second['next_after_sequence'])
    assert [row['sequence'] for row in final['items']] == [4]
    assert final['next_after_sequence'] is None
    assert first['integrity'] == 'event_digests_verified' and first['complete_chain_verified'] is False


@pytest.mark.parametrize('query,count', [
    ({'run_id': 'run-1'}, 2), ({'task_id': 'task-run-2'}, 1), ({'trace_id': 'trace-run-2'}, 1),
    ({'channel_id': 'hub-result-memory'}, 1), ({'scope': MemoryScope.run}, 3),
    ({'scope': MemoryScope.task}, 0), ({'run_id': 'foreign-run'}, 0),
])
def test_all_operator_filters_apply_to_sql(operator, query, count):
    assert len(page(operator, **query)['items']) == count


def test_viewer_and_foreign_project_claim_are_denied_before_repository_read(operator, monkeypatch):
    _, repository, service, principal, _ = operator
    monkeypatch.setattr(repository, 'project_events', lambda **values: pytest.fail('unauthorized SQL read'))
    for identity in [replace(principal, subject_id='viewer'), replace(principal, project_id='project-b')]:
        with pytest.raises((PermissionError, RuntimeError)):
            service.page(principal=identity, project_id='project-a', query=MemoryAuditQuery())


def test_extra_payload_content_never_appears_in_operator_projection(operator):
    _, repository, _, _, _ = operator
    payload = repository.list_events(tenant_id='tenant-a')[0]['payload']
    payload['raw_memory_content'] = 'synthetic-private-canary'
    repository.append(payload, created_at=101, retention_until=3701)
    assert 'synthetic-private-canary' not in repr(page(operator))


def test_run_filter_selects_accessing_run_even_when_source_namespace_has_an_older_run(operator):
    _, repository, _, _, _ = operator
    payload = repository.list_events(tenant_id='tenant-a')[0]['payload']
    payload['run_id'] = 'current-reader-run'
    payload['target']['security']['scope'] = 'task'
    repository.append(payload, created_at=101, retention_until=3701)
    items = page(operator, run_id='current-reader-run')['items']
    assert len(items) == 1
    assert items[0]['run_id'] == 'current-reader-run'
    assert items[0]['namespace']['run_id'] == 'run-1'


def test_tampered_audit_event_is_refused(operator):
    engine, _, _, _, _ = operator
    with Session(engine) as session, session.begin():
        session.exec(update(MemoryAuditEventDB).where(MemoryAuditEventDB.tenant_id == 'tenant-a',
            MemoryAuditEventDB.sequence == 1).values(event_hash='b' * 64))
    with pytest.raises(ValueError, match='chain_invalid'):
        page(operator)


def test_membership_revocation_blocks_the_next_page(operator):
    engine, _, _, _, _ = operator
    first = page(operator, page_size=1)
    with Session(engine) as session, session.begin():
        member = session.get(ProjectMembershipDB, ('tenant-a', 'project-a', 'operator'))
        member.state = 'revoked'
    with pytest.raises(RuntimeError):
        page(operator, page_size=1, after_sequence=first['next_after_sequence'])


def test_missing_authenticated_tenant_is_denied_before_sql(operator, monkeypatch):
    _, repository, service, principal, _ = operator
    monkeypatch.setattr(repository, 'project_events', lambda **values: pytest.fail('unscoped SQL read'))
    with pytest.raises(PermissionError, match='principal_scope_required'):
        service.page(principal=replace(principal, tenant_id=None), project_id='project-a', query=MemoryAuditQuery())


def test_route_uses_authenticated_project_scope_and_same_readmodel(operator):
    _, _, _, _, app = operator
    response = app.test_client().get('/api/projects/project-a/memory-audit/events?run_id=run-1',
                                     headers={'Authorization': 'Bearer synthetic-operator-token'})
    assert response.status_code == 200
    assert response.json['data'] == page(operator, run_id='run-1')
    assert app.test_client().get('/api/projects/project-a/memory-audit/events').status_code == 401


@pytest.mark.parametrize('query', ['page_size=201', 'after_sequence=-1', 'scope=unknown',
                                  'tenant_id=tenant-b', 'page_size=1&page_size=2', 'run_id='])
def test_route_rejects_unknown_coercive_or_duplicate_filters(operator, query):
    _, _, _, _, app = operator
    response = app.test_client().get('/api/projects/project-a/memory-audit/events?' + query,
                                    headers={'Authorization': 'Bearer synthetic-operator-token'})
    assert response.status_code == 422


def test_worker_role_and_project_without_membership_cannot_expose_audit(operator):
    _, _, _, _, app = operator
    client = app.test_client()
    headers = {'Authorization': 'Bearer synthetic-operator-token'}
    assert client.get('/api/projects/project-b/memory-audit/events', headers=headers).status_code == 404
    app.config['ROLE'] = 'worker'
    assert client.get('/api/projects/project-a/memory-audit/events', headers=headers).status_code == 403
