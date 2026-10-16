"""Headless integration: real registry, Hub coordination and fake worker boundary."""
import json
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB, HubSourceEvidenceIdentityDB
from agent.repositories.evidence_identity import SqlEvidenceIdentityRepository
from agent.routes.tasks.autopilot_task_dispatcher_helpers import TaskDispatchResult
from agent.services.hub_evidence_gate_service import HubEvidenceGateService
from agent.services.hub_evidence_registry_service import HubEvidenceRegistryService
from agent.services.self_development.contracts import form
from agent.services.self_development.coordinator import DevelopmentCoordinator
from agent.services.self_development.policy import DevelopmentPolicy
from agent.services.self_development.workspace import git


@pytest.fixture()
def runtime(tmp_path, monkeypatch):
    root = tmp_path / 'private'
    checkout = root / 'checkout'
    checkout.mkdir(parents=True)
    git(checkout, 'init', '-b', 'ananta/self-development')
    git(checkout, 'config', 'user.name', 'Test')
    git(checkout, 'config', 'user.email', 'test@example.invalid')
    (checkout / 'README.md').write_text('baseline\n')
    git(checkout, 'add', 'README.md')
    git(checkout, 'commit', '-m', 'docs(docs): establish synthetic test baseline')
    value = {'enabled': True, 'automatic_publication': True,
        'remote_url': 'https://github.com/ananta888/ananta.git', 'branch': 'ananta/self-development',
        'control_root': str(root), 'workspace_root': str(tmp_path / 'workers'), 'team_id': 'team',
        'tenant_id': 'test-tenant', 'max_tasks': 2, 'allowed_roots': ['tests'],
        'test_profiles': {'self-development': {'command': 'python -m pytest'}}}
    policy_file = tmp_path / 'policy.json'
    policy_file.write_text(json.dumps(value))
    database = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(database, tables=[HubSourceEvidenceIdentityDB.__table__,
                                                 HubRunEvidenceIdentityDB.__table__])
    class SyntheticRegistry(HubEvidenceRegistryService):
        # Explicit test-only policy double. Persisted fixtures remain synthetic;
        # they can never satisfy the production Registry's release verifier.
        def register_source(self, **values):
            return super().register_source(**{**values, 'evidence_scope': 'test', 'synthetic': True})

        def reserve_run(self, **values):
            return super().reserve_run(**{**values, 'evidence_scope': 'test', 'synthetic': True})

        def verify_release_binding(self, **values):
            assert values['required_scope'] == 'local'
            actual = super().verify_release_binding(**values)
            return SimpleNamespace(verified=actual.reason_code == 'evidence_run_test_scope_forbidden',
                                   reason_code='synthetic_test_policy')

    registry = SyntheticRegistry(SqlEvidenceIdentityRepository(database))
    ingested = []
    published = []

    def publish(item, verify, persist):
        verify()
        published.append(item['task_id'])
        item['status'] = 'published'
        persist()

    service = DevelopmentCoordinator(DevelopmentPolicy(policy_file), gate=HubEvidenceGateService(registry),
        registry=registry, queue=SimpleNamespace(ingest_task=lambda **kw: ingested.append(kw)),
        publisher=SimpleNamespace(publish=publish))
    monkeypatch.setattr('agent.services.self_development.coordinator.update_local_task_status', lambda *a, **kw: None)
    monkeypatch.setattr(service, '_verify_worker_result', lambda *a: None)
    return service, ingested, published


def execute(service, *, passed=True, extra=False, stop=False):
    item = next(iter(service.journal.read()['items'].values()))
    task = SimpleNamespace(id=item['task_id'], status='todo')
    worker = SimpleNamespace(url='http://registered-worker:5000')

    def run():
        context = task.worker_execution_context
        assert context['hub_evidence_assignment']['run_id'].startswith('RUN_')
        from pathlib import Path
        workspace = Path(context['workspace']['output_dir'])
        assert not (workspace / '.git').exists()
        (workspace / 'tests/self_development').mkdir(parents=True)
        (workspace / 'tests/self_development/test_example.py').write_text('def test_ok():\n    assert True\n')
        if extra:
            (workspace / 'README.md').write_text('not authorized')
        if stop:
            service.stop()
        return TaskDispatchResult(task.id, dispatched=True, completed=passed, failed=not passed)

    return service.dispatch(task, worker, run)


def test_full_hub_execution_reserves_registry_before_worker_and_publishes(runtime):
    service, ingested, published = runtime
    assert service.import_tasks(form())['added'] == 1
    assert ingested[0]['status'] == 'paused'
    assert service.import_tasks(form())['added'] == 0
    service.start()
    assert execute(service).completed
    item = service.status()['items'][0]
    assert published == [item['task_id']]
    assert item['outcome']['verified'] is True


@pytest.mark.parametrize('option', ['failed_test', 'extra_file', 'operator_stop'])
def test_failed_or_revoked_execution_never_publishes(runtime, option):
    service, _, published = runtime
    service.import_tasks(form())
    service.start()
    result = execute(service, passed=option != 'failed_test', extra=option == 'extra_file',
                     stop=option == 'operator_stop')
    assert result.failed
    assert published == []
    assert service.status()['counts'] == {'failed': 1}
