"""Independent review receipts remain pinned to real Hub Registry bindings."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from typing import Literal

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB, HubSourceEvidenceIdentityDB
from agent.repositories.evidence_identity import SqlEvidenceIdentityRepository
from agent.services.hub_evidence_gate_service import canonical_evidence_digest
from agent.services.hub_evidence_registry_service import (
    HubEvidenceRegistryError,
    HubEvidenceRegistryService,
)
from agent.services.self_development.acceptance_review import (
    AcceptanceReviewError,
    AcceptanceReviewReceipt,
    AcceptanceReviewRequest,
    AcceptanceReviewResult,
    require_acceptance_review,
    verify_acceptance_review,
)
from agent.services.self_development.contracts import form
from agent.services.self_development.coordinator import DevelopmentCoordinator
from agent.services.self_development.policy import DevelopmentPolicy
from agent.services.self_development.workspace import git
from tests.self_development.test_execution import execute, runtime


@pytest.fixture
def registry(runtime) -> HubEvidenceRegistryService:
    return runtime[0].registry


@pytest.fixture
def request_snapshot(runtime) -> AcceptanceReviewRequest:
    return AcceptanceReviewRequest(
        task_id='implementation-task',
        repository_revision=git(runtime[0].policy.checkout, 'rev-parse', 'HEAD'),
        author_worker_url='http://author-worker:5000',
        requirements_json=json.dumps({'acceptance_criteria': ['Compare 30 cases', 'Report baseline']}),
        changed_files=(('agent/services/memory.py', canonical_evidence_digest('source bytes')),),
    )


@dataclass(frozen=True, slots=True)
class RegistryReviewer:
    """Trusted test adapter reserves a separate synthetic run before producing results."""

    registry: HubEvidenceRegistryService
    worker_url: str = 'http://review-worker:5000'
    result: AcceptanceReviewResult = AcceptanceReviewResult(True, (True, True), ())
    terminal_state: Literal['succeeded', 'failed', 'cancelled'] = 'succeeded'
    input_digest: str | None = None
    review_task_id: str = 'independent-review-task'
    review_revision: str | None = None

    def review(self, request: AcceptanceReviewRequest) -> AcceptanceReviewReceipt:
        source = self.registry.register_source(
            tenant_id='test-tenant', project_id='ananta-self-development',
            origin_type='acceptance-review-test', origin_digest=request.digest,
            content_digest=request.digest, policy_digest=canonical_evidence_digest('test-policy'),
            evidence_scope='test', synthetic=True,
        )
        reserved = self.registry.reserve_run(
            tenant_id='test-tenant', project_id='ananta-self-development',
            task_id=self.review_task_id, assignment_id='separate-review-assignment',
            dispatch_lease_id='separate-review-lease',
            repository_revision=self.review_revision or request.repository_revision,
            input_digest=self.input_digest or request.digest,
            execution_profile_digest=canonical_evidence_digest('acceptance-review-test'),
            environment_digest=canonical_evidence_digest(
                {'worker': self.worker_url, 'phase': 'acceptance-review'}),
            source_ids=[source.source_id], evidence_scope='test', synthetic=True,
            idempotency_key='review-test-' + request.digest,
        )
        completed = self.registry.record_result(
            tenant_id='test-tenant', project_id='ananta-self-development',
            run_id=reserved.run_id, assignment_id=reserved.assignment_id,
            dispatch_lease_id=reserved.dispatch_lease_id, terminal_state=self.terminal_state,
            result_digest=canonical_evidence_digest(asdict(self.result)),
        )
        return AcceptanceReviewReceipt(completed, self.worker_url, self.result)


def test_independent_review_accepts_exact_registered_snapshot(registry, request_snapshot) -> None:
    # Given separately assigned review execution under the explicit synthetic fixture policy.
    reviewer = RegistryReviewer(registry)

    # When all criteria pass against the exact implementation snapshot.
    receipt = require_acceptance_review(request_snapshot, criteria_count=2, reviewer=reviewer,
                                        registry=registry, tenant_id='test-tenant')

    # Then the receipt binds the complete request and a distinct Hub-reserved Task.
    assert receipt.run.input_digest == request_snapshot.digest
    assert receipt.run.repository_revision == request_snapshot.repository_revision
    assert receipt.run.task_id != request_snapshot.task_id
    assert receipt.run.synthetic is True
    assert receipt.result.criteria_passed == (True, True)


def test_missing_reviewer_blocks_acceptance(registry, request_snapshot) -> None:
    # Given no configured independent review adapter.
    # When unattended execution reaches acceptance.
    with pytest.raises(AcceptanceReviewError, match='reviewer_unavailable'):
        require_acceptance_review(request_snapshot, criteria_count=2, reviewer=None,
                                  registry=registry, tenant_id='test-tenant')


@pytest.mark.parametrize('same_worker', [True, False])
def test_self_review_is_rejected(registry, request_snapshot, same_worker: bool) -> None:
    # Given either the implementation Worker or implementation Task posing as reviewer.
    reviewer = RegistryReviewer(
        registry,
        worker_url=request_snapshot.author_worker_url if same_worker else 'http://review-worker:5000',
        review_task_id='review-task' if same_worker else request_snapshot.task_id,
    )

    # When it returns even a genuinely registered successful run.
    with pytest.raises(AcceptanceReviewError, match='self_review'):
        require_acceptance_review(request_snapshot, criteria_count=2, reviewer=reviewer,
                                  registry=registry, tenant_id='test-tenant')


@pytest.mark.parametrize('result', [
    AcceptanceReviewResult(True, (True,), ()),
    AcceptanceReviewResult(True, (True, False), ()),
    AcceptanceReviewResult(False, (True, True), ()),
    AcceptanceReviewResult(True, (True, True), ('Missing baseline',)),
])
def test_incomplete_or_negative_criteria_block_acceptance(registry, request_snapshot, result) -> None:
    # Given an incomplete, negative or qualified independent review.
    reviewer = RegistryReviewer(registry, result=result)

    # When the two required criteria are checked.
    with pytest.raises(AcceptanceReviewError, match='criteria_not_satisfied'):
        require_acceptance_review(request_snapshot, criteria_count=2, reviewer=reviewer,
                                  registry=registry, tenant_id='test-tenant')


@pytest.mark.parametrize('change', ['hash', 'requirements', 'revision'])
def test_stored_review_cannot_authorize_mutated_request(registry, request_snapshot, change: str) -> None:
    # Given a completed receipt for the original source snapshot and requirements.
    receipt = RegistryReviewer(registry).review(request_snapshot)
    replacements = {
        'hash': {'changed_files': (('agent/services/memory.py', canonical_evidence_digest('other bytes')), )},
        'requirements': {'requirements_json': json.dumps({'acceptance_criteria': ['Different criterion']})},
        'revision': {'repository_revision': 'f' * 40},
    }
    mutated = replace(request_snapshot, **replacements[change])

    # When publication revalidates that receipt against changed input.
    with pytest.raises(HubEvidenceRegistryError, match='binding_mismatch'):
        verify_acceptance_review(mutated, receipt, criteria_count=2,
                                 registry=registry, tenant_id='test-tenant')


@pytest.mark.parametrize('state', ['failed', 'cancelled'])
def test_failed_or_revoked_review_run_cannot_authorize_publication(
    registry, request_snapshot, state,
) -> None:
    # Given a separately reserved review whose authoritative terminal state denies success.
    reviewer = RegistryReviewer(registry, terminal_state=state)

    # When its claimed positive criteria are presented for publication.
    with pytest.raises(HubEvidenceRegistryError, match='result_unavailable'):
        require_acceptance_review(request_snapshot, criteria_count=2, reviewer=reviewer,
                                  registry=registry, tenant_id='test-tenant')


def test_run_reserved_for_other_input_is_rejected(registry, request_snapshot) -> None:
    # Given a genuine run bound to other input despite a positive review result.
    reviewer = RegistryReviewer(registry, input_digest=canonical_evidence_digest('other input'))

    # When the receipt is submitted for this implementation snapshot.
    with pytest.raises(HubEvidenceRegistryError, match='binding_mismatch'):
        require_acceptance_review(request_snapshot, criteria_count=2, reviewer=reviewer,
                                  registry=registry, tenant_id='test-tenant')


def test_review_of_another_revision_is_rejected(registry, request_snapshot) -> None:
    # Given a genuine run with the requested input digest but another repository revision.
    reviewer = RegistryReviewer(registry, review_revision='e' * 40)

    # When acceptance checks the immutable run's repository binding.
    with pytest.raises(AcceptanceReviewError, match='snapshot_mismatch'):
        require_acceptance_review(request_snapshot, criteria_count=2, reviewer=reviewer,
                                  registry=registry, tenant_id='test-tenant')


def test_unknown_registered_elsewhere_run_is_rejected(registry, request_snapshot) -> None:
    # Given a legitimate Hub-issued receipt unknown to a different empty Registry.
    receipt = RegistryReviewer(registry).review(request_snapshot)
    database = create_engine('sqlite://', poolclass=StaticPool)
    SQLModel.metadata.create_all(database, tables=[HubSourceEvidenceIdentityDB.__table__,
                                                 HubRunEvidenceIdentityDB.__table__])
    other_registry = HubEvidenceRegistryService(SqlEvidenceIdentityRepository(database))

    # When another Registry is asked to authorize that unregistered identity.
    with pytest.raises(HubEvidenceRegistryError, match='result_unavailable'):
        verify_acceptance_review(request_snapshot, receipt, criteria_count=2,
                                 registry=other_registry, tenant_id='test-tenant')


def test_production_registry_rejects_synthetic_fixture_receipt(registry, request_snapshot) -> None:
    # Given a fixture receipt which only the explicit test policy can accept.
    receipt = RegistryReviewer(registry).review(request_snapshot)
    production_registry = HubEvidenceRegistryService(registry._repository)

    # When the real production verifier examines that same registered receipt.
    with pytest.raises(AcceptanceReviewError, match='evidence_unverified'):
        verify_acceptance_review(request_snapshot, receipt, criteria_count=2,
                                 registry=production_registry, tenant_id='test-tenant')


def _enable_independent_review(service: DevelopmentCoordinator) -> None:
    service.policy.value['quality_gates'] = {'require_acceptance_review': True}
    service.policy.path.write_text(json.dumps(service.policy.value))
    service.policy = DevelopmentPolicy(service.policy.path)
    service.reviewer = RegistryReviewer(service.registry)


def test_coordinator_reviews_then_seals_tracked_todo_before_publication(runtime) -> None:
    # Given a real tracked TODO and an explicitly required independent acceptance review.
    service, _, published = runtime
    _enable_independent_review(service)
    document = form()
    document['tasks'][0]['acceptance_criteria'] = ['Verify behavior', 'Run regression checks']
    source = service.policy.checkout / 'todos/active/todo.review.json'
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps(document))
    git(service.policy.checkout, 'add', '--', 'todos/active/todo.review.json')
    git(service.policy.checkout, 'commit', '-m', 'test(autopilot): add synthetic review task')
    service.import_tasks()
    service.start()

    # When execution returns a separately reserved, successful acceptance review.
    result = execute(service)

    # Then reviewed implementation bytes publish alongside subsequent Hub completion metadata.
    item = service.status()['items'][0]
    assert result.completed
    assert published == [item['task_id']]
    reviewed_hashes = dict(item['acceptance_review_request']['changed_files'])
    assert set(reviewed_hashes) == {'tests/self_development/test_example.py'}
    assert reviewed_hashes == item['outcome']['execution']['hashes']
    assert set(item['hashes']) == {
        'tests/self_development/test_example.py', 'todos/archiv/todo.review.json'}
    sealed_todo = service.policy.root / 'sealed' / item['task_id'] / 'todos/archiv/todo.review.json'
    assert json.loads(sealed_todo.read_text())['tasks'][0]['status'] == 'done'
    state = service.journal.read()
    service._verify(state, state['items'][item['key']])


@pytest.mark.parametrize('mutation', ['revision', 'hashes', 'criteria', 'request_hashes'])
def test_coordinator_revalidation_rejects_mutated_persisted_review_snapshot(runtime, mutation: str) -> None:
    # Given a published implementation with its serialized independent review and Registry records.
    service, _, published = runtime
    _enable_independent_review(service)
    document = form()
    document['tasks'][0]['acceptance_criteria'] = ['Verify behavior', 'Run regression checks']
    service.import_tasks(document)
    service.start()
    assert execute(service).completed
    state = service.journal.read()
    item = next(iter(state['items'].values()))
    assert published == [item['task_id']]
    mutations = {
        'revision': {'revision': 'd' * 40},
        'hashes': {'hashes': {'tests/self_development/test_example.py': canonical_evidence_digest('changed')}},
        'criteria': {'acceptance_criteria': ['Different behavior', 'Different regression']},
        'request_hashes': {'acceptance_review_request': {
            **item['acceptance_review_request'],
            'changed_files': [['tests/self_development/test_example.py', canonical_evidence_digest('changed')]],
        }},
    }
    item.update(mutations[mutation])

    # When publication replays the stored receipt against altered state.
    with pytest.raises(AcceptanceReviewError, match='snapshot_mismatch'):
        service._verify(state, item)
