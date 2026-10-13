"""Hub-issued approved-byte source is reserved before execution; test policy only."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlmodel import Session, select

from agent.db_models import TaskDB
from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB, HubSourceEvidenceIdentityDB
from agent.services.pi_run_evidence_reservation import PiRunEvidenceReservationService
from ananta_contracts.native_context_bundle import (
    NativeApprovedContext,
    NativeContextBundleProjection,
    native_context_digest,
)
from tests.pi.test_pi_result_completion_policy import admission as admission  # noqa: F401
from tests.pi.test_pi_run_evidence_reservation import reservation as reservation  # noqa: F401


@pytest.fixture
def contextual(reservation):
    with Session(reservation.admission.engine) as session:
        task = session.get(TaskDB, reservation.admission.task_id)
        task.context_bundle_id = 'approved-test-bundle'
        session.commit()
    projection = NativeContextBundleProjection.create(hub_task_id=reservation.admission.task_id,
        bundle_id='approved-test-bundle', command=reservation.admission.command.to_dict(),
        approved=NativeApprovedContext(content='synthetic policy-approved bytes', policy_digest='d'*64))
    reader = Mock(read=Mock(return_value=projection))
    service = PiRunEvidenceReservationService(manifests=reservation.service._manifests,
        registry=reservation.registry, authority=reservation.service._authority, contexts=reader,
        clock=lambda: reservation.admission.clock.now)
    return SimpleNamespace(value=reservation, service=service, reader=reader, projection=projection)


def test_approved_context_bytes_have_hub_source_before_run_reservation(contextual):
    value = contextual.value
    with Session(value.admission.engine) as session:
        run = contextual.service.reserve(session=session, **value.values)
    assert len(run.source_ids) == 3 and run.state == 'reserved' and run.synthetic is True
    with Session(value.admission.engine) as session:
        source = session.exec(select(HubSourceEvidenceIdentityDB).where(
            HubSourceEvidenceIdentityDB.origin_type == 'pi_native_approved_context')).one()
        assert source.source_id in run.source_ids
        assert source.content_digest == contextual.projection.content_digest
        assert source.policy_digest == native_context_digest({'pi_policy_digest': value.values['policy_digest'],
            'context_policy_digest': contextual.projection.policy_digest})
    assert contextual.reader.read.call_args.kwargs['worker_id'] == 'worker-1'


@pytest.mark.parametrize('change', ['missing_reader', 'foreign_bundle', 'foreign_task', 'command', 'content_digest'])
def test_unknown_or_mismatched_context_never_reserves_run(contextual, change):
    value = contextual.value
    service = contextual.service
    if change == 'missing_reader':
        service = value.service
    else:
        field = {'foreign_bundle': 'bundle_id', 'foreign_task': 'hub_task_id',
                 'command': 'command_digest', 'content_digest': 'content_digest'}[change]
        replacement = 'f'*64 if 'digest' in field else 'foreign-binding'
        contextual.reader.read.return_value = replace(contextual.projection, **{field: replacement})
    with pytest.raises(ValueError):
        with Session(value.admission.engine) as session:
            service.reserve(session=session, **value.values)
    with Session(value.admission.engine) as session:
        assert session.exec(select(HubRunEvidenceIdentityDB)).all() == []
        assert session.exec(select(HubSourceEvidenceIdentityDB).where(
            HubSourceEvidenceIdentityDB.origin_type == 'pi_native_approved_context')).all() == []


def test_atomic_dispatch_reserves_context_source_in_the_same_commit(contextual):
    from agent.db_models.pi_dispatch_evidence import PiDispatchEvidenceDB
    from tests.pi.test_pi_dispatch_transaction import assigned, preparation

    value = contextual.value
    prepared = preparation(value, contexts=contextual.reader).prepare(
        task_id=value.admission.task_id, assignment=assigned(value))
    assert len(prepared.payload['evidence_assignment']['source_ids']) == 3
    with Session(value.admission.engine) as session:
        run = session.exec(select(HubRunEvidenceIdentityDB)).one()
        assert run.source_ids == prepared.payload['evidence_assignment']['source_ids']
        assert session.exec(select(PiDispatchEvidenceDB)).one().projection['run_id'] == run.run_id


def test_missing_context_port_rolls_back_dispatch_without_started_task(contextual):
    from agent.db_models.native_dispatch import NativeDispatchDB
    from tests.pi.test_pi_dispatch_transaction import assigned, preparation

    value = contextual.value
    with pytest.raises(ValueError, match='projection_required'):
        preparation(value).prepare(task_id=value.admission.task_id, assignment=assigned(value))
    with Session(value.admission.engine) as session:
        assert session.exec(select(HubRunEvidenceIdentityDB)).all() == []
        assert session.exec(select(NativeDispatchDB)).all() == []
        assert session.get(TaskDB, value.admission.task_id).status == 'assigned'


@pytest.mark.parametrize('change', [None, 'content', 'policy', 'removed_reference'])
def test_current_reserved_context_read_and_completion_reject_changed_bytes(contextual, change):
    from tests.pi.test_pi_dispatch_transaction import assigned, preparation
    from tests.pi.test_pi_result_completion_policy import complete

    value = contextual.value
    service = preparation(value, contexts=contextual.reader)
    service.prepare(task_id=value.admission.task_id, assignment=assigned(value))
    if change in {'content', 'policy'}:
        content = 'changed bytes' if change == 'content' else contextual.projection.content
        approved = NativeApprovedContext(content=content,
            policy_digest='e'*64 if change == 'policy' else contextual.projection.policy_digest)
        contextual.reader.read.return_value = NativeContextBundleProjection.create(
            hub_task_id=value.admission.task_id, bundle_id=contextual.projection.bundle_id,
            command=value.admission.command.to_dict(), approved=approved)
    elif change == 'removed_reference':
        with Session(value.admission.engine) as session:
            task = session.get(TaskDB, value.admission.task_id)
            task.context_bundle_id = None
            session.commit()
    if change is None:
        assert service.reserved_context(task_id=value.admission.task_id) == contextual.projection
    else:
        with pytest.raises(ValueError):
            service.reserved_context(task_id=value.admission.task_id)
    complete(value.admission)
    if change is None:
        assert service.complete_reserved(task_id=value.admission.task_id).state == 'succeeded'
        value.admission.clock.now += 1000
        assert service.complete_reserved(task_id=value.admission.task_id).state == 'succeeded'
    else:
        with pytest.raises(ValueError):
            service.complete_reserved(task_id=value.admission.task_id)
        with Session(value.admission.engine) as session:
            assert session.exec(select(HubRunEvidenceIdentityDB)).one().state == 'reserved'
    with Session(value.admission.engine) as session:
        assert len(session.exec(select(HubSourceEvidenceIdentityDB).where(
            HubSourceEvidenceIdentityDB.origin_type == 'pi_native_approved_context')).all()) == 1
