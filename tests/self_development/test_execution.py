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
        'tenant_id': 'test-tenant', 'max_tasks': 2, 'max_attempts': 1, 'allowed_roots': ['tests'],
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


def test_import_binds_codecompass_capability(runtime):
    service, ingested, _ = runtime
    service.import_tasks(form())
    capabilities = ingested[0]['extra_fields']['required_capabilities']
    assert 'codecompass' in capabilities
    assert {'coding', 'testing', 'workflow.adapter.native'} <= set(capabilities)


def test_blocked_catalog_records_are_paused_in_general_hub_queue(runtime):
    service, ingested, _ = runtime
    service.import_tasks({'tasks': [{'id': 'LEGACY', 'status': 'blocked'}]})
    assert service.status()['counts'] == {'blocked': 1}
    # Generic dependency reconciliation may unblock `blocked` without edges.
    # Paused is deliberately outside that reconciler's admission contract.
    assert ingested[0]['status'] == 'paused'


def test_start_releases_ready_tasks_only_and_keeps_session_budget(runtime, monkeypatch):
    service, _, _ = runtime
    document = form()
    document['tasks'].append({'id': 'LEGACY', 'status': 'todo'})
    service.import_tasks(document)
    updates = []
    monkeypatch.setattr('agent.services.self_development.coordinator.update_local_task_status',
                        lambda task_id, status: updates.append((task_id, status)))
    service.start()
    first = service.status()['session']
    service.start()
    assert service.status()['session']['id'] == first['id']
    assert len(updates) == 2
    ready_id = next(i['task_id'] for i in service.status()['items'] if i['status'] == 'ready')
    assert updates == [(ready_id, 'todo'), (ready_id, 'todo')]


def test_missing_contract_reason_is_preserved_when_dependencies_are_open(runtime):
    service, ingested, _ = runtime
    service.import_tasks({'tasks': [{'id': 'LEGACY', 'status': 'todo', 'depends_on': ['EXTERNAL']}]})
    assert service.status()['items'][0]['reason'] == 'needs_execution_contract'
    assert ingested[0]['status'] == 'paused'


def test_failed_attempt_gets_fresh_task_and_bounded_retry(runtime):
    service, ingested, published = runtime
    service.policy.value['max_attempts'] = 2
    # Policy remains immutable; use the fixture policy file as operator authority.
    service.policy.path.write_text(json.dumps(service.policy.value))
    from agent.services.hub_evidence_gate_service import canonical_evidence_digest
    service.policy.digest = canonical_evidence_digest(service.policy.value)
    service.import_tasks(form())
    old_id = service.status()['items'][0]['task_id']
    service.start()
    assert execute(service, passed=False).failed
    pending = service.status()['items'][0]
    assert pending['task_id'] != old_id
    assert service.manages(old_id)
    rejected = service.dispatch(SimpleNamespace(id=old_id), SimpleNamespace(url='worker'),
        lambda: pytest.fail('stale attempts must never invoke a Worker'))
    assert rejected.failure_type == 'stale_development_attempt'
    assert pending['status'] == 'ready'
    assert pending['attempts'][0]['task_id'] == old_id
    assert execute(service, passed=False).failed
    assert service.status()['items'][0]['status'] == 'failed'
    assert len(ingested) == 2
    assert published == []


class _FakePlanner:
    def __init__(self, proposal):
        self.proposal = dict(proposal)
        self.calls = []

    def propose(self, *, item, envelope, error=None):
        self.calls.append((item['todo_id'], envelope, error))
        return dict(self.proposal)


class _ScriptedPlanner:
    """Returns queued proposals in order, recording the correction feedback."""

    def __init__(self, proposals):
        self.proposals = [dict(p) for p in proposals]
        self.errors = []

    def propose(self, *, item, envelope, error=None):
        self.errors.append(error)
        return dict(self.proposals.pop(0))


def enable_planning(service, planner, **planning):
    from agent.services.hub_evidence_gate_service import canonical_evidence_digest
    service.policy.value['planning'] = {'enabled': True, 'max_files': 4,
        'allowed_test_profiles': ['self-development'], 'default_test_profile': 'self-development',
        'commit_scopes': ['autopilot'], **planning}
    service.policy.path.write_text(json.dumps(service.policy.value))
    service.policy.digest = canonical_evidence_digest(service.policy.value)
    service.planner = planner


def test_planning_admits_contract_less_todo_and_publishes(runtime):
    service, _, published = runtime
    planner = _FakePlanner({'files': ['tests/self_development/test_example.py'],
        'test_profile': 'self-development', 'commit_message': 'test(autopilot): planned coverage'})
    enable_planning(service, planner)
    service.import_tasks({'tasks': [{'id': 'LEGACY', 'status': 'todo'}]})
    assert service.status()['items'][0]['status'] == 'ready'
    service.start()
    assert execute(service).completed
    item = service.status()['items'][0]
    assert item['planned'] is True
    assert item['contract']['files'] == ['tests/self_development/test_example.py']
    assert published == [item['task_id']]
    assert planner.calls[0][0] == 'LEGACY'


def test_planning_proposal_outside_write_scope_is_rejected(runtime):
    service, _, published = runtime
    enable_planning(service, _FakePlanner({'files': ['agent/out.py'],
        'test_profile': 'self-development', 'commit_message': 'test(autopilot): too wide'}))
    service.import_tasks({'tasks': [{'id': 'LEGACY', 'status': 'todo'}]})
    service.start()
    assert execute(service).failed
    assert published == []
    assert service.status()['items'][0]['status'] == 'failed'
    assert 'task_scope_not_authorized' in service.status()['items'][0]['reason']


def test_planning_proposal_with_denied_commit_scope_is_rejected(runtime):
    service, _, published = runtime
    enable_planning(service, _FakePlanner({'files': ['tests/self_development/test_example.py'],
        'test_profile': 'self-development', 'commit_message': 'test(worker): wrong scope'}))
    service.import_tasks({'tasks': [{'id': 'LEGACY', 'status': 'todo'}]})
    service.start()
    assert execute(service).failed
    assert published == []
    assert service.status()['items'][0]['reason'] == 'planned_admission_failed:planned_commit_scope_denied'


def test_planning_retries_once_with_correction_feedback(runtime):
    service, _, published = runtime
    good = {'files': ['tests/self_development/test_example.py'], 'test_profile': 'self-development',
            'commit_message': 'test(autopilot): planned coverage'}
    bad = dict(good, commit_message='nope')
    planner = _ScriptedPlanner([bad, good])
    enable_planning(service, planner, max_attempts=2)
    service.import_tasks({'tasks': [{'id': 'LEGACY', 'status': 'todo'}]})
    service.start()
    assert execute(service).completed
    assert planner.errors[0] is None
    assert 'needs_scoped_commit_message' in (planner.errors[1] or '')
    assert published


def test_planning_exhausts_attempts_with_bounded_reason(runtime):
    service, _, published = runtime
    bad = {'files': ['tests/self_development/test_example.py'], 'test_profile': 'self-development',
           'commit_message': 'nope'}
    enable_planning(service, _ScriptedPlanner([bad, dict(bad)]), max_attempts=2)
    service.import_tasks({'tasks': [{'id': 'LEGACY', 'status': 'todo'}]})
    service.start()
    assert execute(service).failed
    assert published == []
    assert service.status()['items'][0]['reason'].startswith('planned_admission_failed:')


def test_dependency_deadlock_sweep_skips_unpublishable_dependency(runtime):
    service, _, _ = runtime
    enable_planning(service, _FakePlanner({'files': ['tests/self_development/test_example.py'],
        'test_profile': 'self-development', 'commit_message': 'test(autopilot): planned coverage'}))
    service.import_tasks({'tasks': [
        {'id': 'A', 'status': 'blocked'},
        {'id': 'B', 'status': 'todo', 'depends_on': ['A']},
    ]})
    items = {i['todo_id']: i for i in service.status()['items']}
    assert items['A']['status'] == 'blocked'
    assert items['B']['status'] == 'skipped'
    assert items['B']['reason'] == 'dependency_not_published'


def test_skip_unpublished_dependencies_marks_external_dependency_skipped(runtime):
    service, _, _ = runtime
    from agent.services.hub_evidence_gate_service import canonical_evidence_digest
    service.policy.value['skip_unpublished_dependencies'] = True
    service.policy.path.write_text(json.dumps(service.policy.value))
    service.policy.digest = canonical_evidence_digest(service.policy.value)
    document = form()
    document['tasks'][0]['depends_on'] = ['EXTERNAL']
    service.import_tasks(document)
    item = service.status()['items'][0]
    assert item['status'] == 'skipped'
    assert item['reason'] == 'dependency_not_published'
