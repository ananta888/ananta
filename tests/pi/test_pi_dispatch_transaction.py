"""Real Hub SQL rollback across Pi RUN reservation and durable send fencing."""

from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.db_models import TaskDB
from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB, HubSourceEvidenceIdentityDB
from agent.db_models.native_dispatch import NativeDispatchDB
from agent.repositories.evidence_identity_transaction import TransactionEvidenceIdentityRepository
from agent.services.hub_evidence_registry_service import HubEvidenceRegistryService
from agent.services.native_dispatch_journal import SQLAlchemyNativeDispatchJournal
from agent.services.pi_run_evidence_reservation import PiRunEvidenceReservationService
from agent.services.pi_runtime_manifest_admission import PiRuntimeManifestAdmissionService
from tests.pi.test_pi_result_completion_policy import admission as admission  # noqa: F401
from tests.pi.test_pi_run_evidence_reservation import reservation as reservation  # noqa: F401


def prepare(session, fixture):
    registry = HubEvidenceRegistryService(TransactionEvidenceIdentityRepository(session),
        clock=lambda: fixture.admission.clock.now)
    manifests = PiRuntimeManifestAdmissionService(
        deployments=fixture.service._manifests._deployments, registry=registry)
    service = PiRunEvidenceReservationService(manifests=manifests, registry=registry,
        authority=fixture.service._authority, clock=lambda: fixture.admission.clock.now)
    run = service.reserve(session=session, **fixture.values)
    projection = service.assignment_projection(session=session, run_id=run.run_id,
        expected_binding_digest=run.binding_digest, **fixture.values)
    task = session.get(TaskDB, fixture.admission.task_id)
    assignment = SimpleNamespace(worker_id='worker-1', worker_url=task.assigned_agent_url, revision=1)
    claim = SQLAlchemyNativeDispatchJournal(fixture.admission.engine).begin_in_transaction(
        session=session, task=task, assignment=assignment)
    task.status = 'in_progress'
    session.flush()
    return run, projection, claim


def test_caller_commit_persists_run_send_fence_and_started_task_together(reservation):
    with Session(reservation.admission.engine) as session, session.begin():
        run, projection, claim = prepare(session, reservation)
        assert projection['run_id'] == run.run_id
        assert claim
    with Session(reservation.admission.engine) as session:
        assert session.get(TaskDB, run.task_id).status == 'in_progress'
        assert session.get(HubRunEvidenceIdentityDB, (run.tenant_id, run.project_id, run.run_id)) is not None
        assert session.exec(select(NativeDispatchDB)).one().claim == claim


def test_caller_rollback_removes_run_input_source_send_fence_and_status(reservation):
    with Session(reservation.admission.engine) as session:
        original_sources = len(session.exec(select(HubSourceEvidenceIdentityDB)).all())
    with pytest.raises(RuntimeError, match='synthetic_commit_failure'):
        with Session(reservation.admission.engine) as session, session.begin():
            prepare(session, reservation)
            raise RuntimeError('synthetic_commit_failure')
    with Session(reservation.admission.engine) as session:
        assert session.get(TaskDB, reservation.admission.task_id).status == 'assigned'
        assert session.exec(select(HubRunEvidenceIdentityDB)).all() == []
        assert session.exec(select(NativeDispatchDB)).all() == []
        assert len(session.exec(select(HubSourceEvidenceIdentityDB)).all()) == original_sources


def test_existing_send_fence_rolls_back_new_registry_reservation(reservation):
    with Session(reservation.admission.engine) as session:
        task = session.get(TaskDB, reservation.admission.task_id)
        SQLAlchemyNativeDispatchJournal(reservation.admission.engine).begin(task=task,
            assignment=SimpleNamespace(worker_id='worker-1', worker_url=task.assigned_agent_url, revision=1))
    with pytest.raises(IntegrityError):
        with Session(reservation.admission.engine) as session, session.begin():
            prepare(session, reservation)
    with Session(reservation.admission.engine) as session:
        assert session.exec(select(HubRunEvidenceIdentityDB)).all() == []
        assert session.get(TaskDB, reservation.admission.task_id).status == 'assigned'
        assert len(session.exec(select(NativeDispatchDB)).all()) == 1


def test_journal_requires_active_caller_transaction(reservation):
    with Session(reservation.admission.engine) as session:
        with pytest.raises(ValueError, match='caller_transaction_required'):
            SQLAlchemyNativeDispatchJournal(reservation.admission.engine).begin_in_transaction(
                session=session, task=SimpleNamespace(id='unused'), assignment=None)


def test_stale_task_projection_cannot_reserve_send_fence(reservation):
    with Session(reservation.admission.engine) as session:
        task = session.get(TaskDB, reservation.admission.task_id)
        detached = task.model_copy(deep=True)
        task.worker_execution_context = {**task.worker_execution_context, 'changed_input': True}
        session.commit()
    with pytest.raises(ValueError, match='current_task_binding_mismatch'):
        with Session(reservation.admission.engine) as session, session.begin():
            SQLAlchemyNativeDispatchJournal(reservation.admission.engine).begin_in_transaction(
                session=session, task=detached,
                assignment=SimpleNamespace(worker_id='worker-1', worker_url=detached.assigned_agent_url, revision=1))
    with Session(reservation.admission.engine) as session:
        assert session.exec(select(NativeDispatchDB)).all() == []


def preparation(fixture, **overrides):
    import base64

    from agent.services.pi_dispatch_preparation import PiDispatchPreparationService
    from ananta_contracts.runtime_authorization_crypto import Ed25519SigningKeyRing

    signer = Ed25519SigningKeyRing({'synthetic': base64.b64encode(b's' * 32).decode()}, active_key_id='synthetic')
    values = dict(engine=fixture.admission.engine, deployments=fixture.service._manifests._deployments,
        authority=fixture.service._authority, policy_digest=lambda task: fixture.values['policy_digest'],
        signing_keys=lambda: signer, clock=lambda: fixture.admission.clock.now)
    values.update(overrides)
    return PiDispatchPreparationService(**values)


def assigned(fixture):
    manifest = fixture.values['manifest']
    return SimpleNamespace(worker_id=manifest.worker_id, worker_url=manifest.worker_url, revision=1)


def test_preparation_returns_signed_projection_only_after_atomic_commit(reservation):
    from ananta_contracts.native_task_execution import verify_native_execution_envelope
    from ananta_contracts.runtime_authorization_crypto import Ed25519VerificationKeyRing

    service = preparation(reservation)
    prepared = service.prepare(task_id=reservation.admission.task_id, assignment=assigned(reservation))
    manifest = reservation.values['manifest']
    verified = verify_native_execution_envelope(prepared.payload,
        key_ring=Ed25519VerificationKeyRing.from_mapping(service._signing_keys().verification_mapping()),
        worker_id=manifest.worker_id, worker_url=manifest.worker_url, now=reservation.admission.clock.now)
    with Session(reservation.admission.engine) as session:
        task = session.get(TaskDB, reservation.admission.task_id)
        assert task.status == 'in_progress'
        assert verified.task['worker_execution_context'] == task.worker_execution_context
        assert session.exec(select(NativeDispatchDB)).one().claim == prepared.claim
        assert session.exec(select(HubRunEvidenceIdentityDB)).one().run_id == verified.evidence_assignment['run_id']


@pytest.mark.parametrize('failure', ['assignment', 'signing', 'journal'])
def test_preparation_failure_never_leaves_partial_registry_or_started_task(reservation, failure):
    assignment = assigned(reservation)
    overrides = {}
    if failure == 'assignment':
        assignment.revision += 1
    elif failure == 'signing':
        def reject():
            raise ValueError('synthetic_signing_unavailable')
        overrides['signing_keys'] = reject
    else:
        with Session(reservation.admission.engine) as session:
            task = session.get(TaskDB, reservation.admission.task_id)
            SQLAlchemyNativeDispatchJournal(reservation.admission.engine).begin(task=task, assignment=assignment)
    with pytest.raises((ValueError, IntegrityError)):
        preparation(reservation, **overrides).prepare(task_id=reservation.admission.task_id, assignment=assignment)
    with Session(reservation.admission.engine) as session:
        assert session.get(TaskDB, reservation.admission.task_id).status == 'assigned'
        assert session.exec(select(HubRunEvidenceIdentityDB)).all() == []
        assert len(session.exec(select(NativeDispatchDB)).all()) == (1 if failure == 'journal' else 0)


def test_commit_failure_returns_no_dispatch_and_rolls_back(reservation):
    from sqlalchemy import event

    def reject_commit(session):
        raise RuntimeError('synthetic_commit_failure')

    event.listen(Session, 'before_commit', reject_commit)
    try:
        with pytest.raises(RuntimeError, match='synthetic_commit_failure'):
            preparation(reservation).prepare(task_id=reservation.admission.task_id, assignment=assigned(reservation))
    finally:
        event.remove(Session, 'before_commit', reject_commit)
    with Session(reservation.admission.engine) as session:
        assert session.get(TaskDB, reservation.admission.task_id).status == 'assigned'
        assert session.exec(select(HubRunEvidenceIdentityDB)).all() == []
        assert session.exec(select(NativeDispatchDB)).all() == []


def test_reconciliation_requires_registry_completion_before_pi_journal_acceptance(reservation):
    from agent.services.native_dispatch_reconciliation import reconcile_native_dispatches
    from tests.pi.test_pi_result_completion_policy import complete

    service = preparation(reservation)
    prepared = service.prepare(task_id=reservation.admission.task_id, assignment=assigned(reservation))
    complete(reservation.admission)
    journal = SQLAlchemyNativeDispatchJournal(reservation.admission.engine)
    values = dict(journal=journal, task_repository=reservation.admission.repository,
                  tenant_id=reservation.values['manifest'].tenant_id, limit=1)
    assert reconcile_native_dispatches(**values)['unresolved'] == 1
    with Session(reservation.admission.engine) as session:
        assert session.exec(select(HubRunEvidenceIdentityDB)).one().state == 'reserved'
        assert session.exec(select(NativeDispatchDB)).one().state == 'sending'
    def close(task_id):
        return service.complete(task_id=task_id, assignment=prepared.payload['evidence_assignment'])

    result = reconcile_native_dispatches(**values, complete_pi=close)
    assert result['accepted'] == 1
    with Session(reservation.admission.engine) as session:
        assert session.exec(select(HubRunEvidenceIdentityDB)).one().state == 'succeeded'
        assert session.exec(select(NativeDispatchDB)).one().state == 'accepted'


def test_result_recovery_does_not_close_pi_journal_without_evidence_ports(reservation):
    from unittest.mock import Mock

    from agent.services.native_dispatch_result_recovery import (
        NativeResultRecoveryPorts,
        recover_native_dispatch_results,
    )
    from tests.pi.test_pi_result_completion_policy import complete

    preparation(reservation).prepare(task_id=reservation.admission.task_id, assignment=assigned(reservation))
    complete(reservation.admission)
    transport = Mock()
    journal = SQLAlchemyNativeDispatchJournal(reservation.admission.engine)
    ports = NativeResultRecoveryPorts(repository=reservation.admission.repository, journal=journal,
        assignments=Mock(), workers=Mock(), signing_keys=Mock(), transport=transport, accept_result=Mock())
    result = recover_native_dispatch_results(ports=ports, tenant_id=reservation.values['manifest'].tenant_id)
    assert result['unresolved'] == 1 and result['accepted'] == 0
    transport.forward_task.assert_not_called()
    with Session(reservation.admission.engine) as session:
        assert session.exec(select(HubRunEvidenceIdentityDB)).one().state == 'reserved'
        assert session.exec(select(NativeDispatchDB)).one().state == 'sending'


def test_reopened_hub_loads_existing_projection_and_completes_without_new_run(reservation):
    from sqlalchemy import create_engine

    from tests.pi.test_pi_result_completion_policy import complete

    prepared = preparation(reservation).prepare(task_id=reservation.admission.task_id, assignment=assigned(reservation))
    engine = create_engine(reservation.admission.engine.url)
    try:
        reopened = preparation(reservation, engine=engine)
        loaded = reopened.reserved_assignment(task_id=reservation.admission.task_id)
        assert loaded == prepared.payload['evidence_assignment']
        complete(reservation.admission)
        assert reopened.complete_reserved(task_id=reservation.admission.task_id).state == 'succeeded'
        reservation.admission.clock.now += 1000
        assert reopened.complete_reserved(task_id=reservation.admission.task_id).state == 'succeeded'
        with Session(engine) as session:
            assert len(session.exec(select(HubRunEvidenceIdentityDB)).all()) == 1
            assert len(session.exec(select(NativeDispatchDB)).all()) == 1
    finally:
        engine.dispose()


@pytest.mark.parametrize('change', ['claim', 'project', 'projection'])
def test_persisted_projection_mutation_cannot_be_used_for_recovery(reservation, change):
    from agent.db_models.pi_dispatch_evidence import PiDispatchEvidenceDB
    from ananta_contracts.hub_evidence import build_hub_evidence_assignment

    service = preparation(reservation)
    service.prepare(task_id=reservation.admission.task_id, assignment=assigned(reservation))
    with Session(reservation.admission.engine) as session:
        row = session.exec(select(PiDispatchEvidenceDB)).one()
        if change == 'projection':
            fields = {key: item for key, item in row.projection.items() if key not in {'schema', 'projection_digest'}}
            fields['dispatch_lease_id'] = 'different-dispatch'
            row.projection = build_hub_evidence_assignment(**fields)
        else:
            setattr(row, 'claim' if change == 'claim' else 'project_id', 'different-binding')
        session.commit()
    with pytest.raises(ValueError):
        service.reserved_assignment(task_id=reservation.admission.task_id)


def test_commit_failure_rolls_back_persisted_evidence_mapping(reservation):
    from sqlalchemy import event

    from agent.db_models.pi_dispatch_evidence import PiDispatchEvidenceDB

    def reject(session):
        raise RuntimeError('synthetic_commit_failure')

    event.listen(Session, 'before_commit', reject)
    try:
        with pytest.raises(RuntimeError, match='synthetic_commit_failure'):
            preparation(reservation).prepare(task_id=reservation.admission.task_id, assignment=assigned(reservation))
    finally:
        event.remove(Session, 'before_commit', reject)
    with Session(reservation.admission.engine) as session:
        assert session.exec(select(PiDispatchEvidenceDB)).all() == []


@pytest.mark.parametrize('foreign_scope', [False, True])
def test_registered_worker_read_matches_exact_task_workflow_scope(reservation, foreign_scope):
    service = preparation(reservation)
    prepared = service.prepare(task_id=reservation.admission.task_id, assignment=assigned(reservation))
    command = reservation.admission.command
    values = dict(task_id=reservation.admission.task_id, expected_command_id=command.command_id,
        expected_input_digest=prepared.payload['evidence_assignment']['binding_digest'],
        expected_worker_id='worker-1', expected_worker_url='http://worker-1:5000',
        expected_tenant_id=command.tenant_id, expected_workflow_id=command.workflow_id,
        expected_run_id=command.run_id, expected_step_id=command.node.node_id)
    from ananta_contracts.native_context_bundle import native_context_digest

    values['expected_input_digest'] = native_context_digest(prepared.payload['task']['worker_execution_context'])
    if foreign_scope:
        values['expected_workflow_id'] = 'foreign-workflow'
        with pytest.raises(ValueError, match='scope_denied'):
            service.reserved_assignment(**values)
    else:
        assert service.reserved_assignment(**values) == prepared.payload['evidence_assignment']
