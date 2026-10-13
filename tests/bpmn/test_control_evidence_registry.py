"""Real SQL Registry admission before synthetic Hub control operations."""

import time
from dataclasses import replace

import pytest
from sqlmodel import Session, select

import agent.database as database
from agent.db_models import ProjectDB, TaskDB
from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB, HubSourceEvidenceIdentityDB
from agent.services.workflow_runtime import HmacKeyRing, SQLAlchemyCheckpointStore
from agent.services.workflow_runtime.security import SignedCheckpoint, WorkflowState
from tests.bpmn.test_run_lease import setup
from tests.bpmn_container.control_evidence import SyntheticControlEvidence


@pytest.fixture
def control(app, monkeypatch, request):
    monkeypatch.setenv('BPMN_REPOSITORY_REVISION', '1' * 40)
    monkeypatch.setenv('BPMN_REPOSITORY_SOURCE_DIGEST', '2' * 64)
    _, _, assignment = setup()
    assignment = replace(assignment, run_id='synthetic-' + request.node.name)
    keys = HmacKeyRing({'synthetic': 's' * 32}, active_key_id='synthetic')
    with Session(database.engine) as session, session.begin():
        session.add(ProjectDB(tenant_id=assignment.plan.tenant_id, project_id='synthetic-control-project',
            name='Synthetic test project', created_by_subject_id='synthetic-hub'))
        session.flush()
        session.add(TaskDB(id=assignment.control_task_id, tenant_id=assignment.plan.tenant_id,
            project_id='synthetic-control-project', task_kind='bpmn_reference_control', status='running'))
    store = SQLAlchemyCheckpointStore(database.engine)
    return SyntheticControlEvidence(engine=database.engine, checkpoints=store, keys=keys), assignment, store, keys


def runs():
    with Session(database.engine) as session:
        return list(session.exec(select(HubRunEvidenceIdentityDB)))


def save_checkpoint(request, store, keys):
    checkpoint = SignedCheckpoint.issue(key_ring=keys, tenant_id=request.plan.tenant_id,
        workflow_id=request.plan.workflow_id, run_id=request.run_id, task_id=request.control_task_id,
        plan_hash=request.plan.plan_hash, policy_version=request.plan.policy_version, runtime_id='synthetic-control',
        runtime_version='1', state=WorkflowState(business_data={'status': 'waiting_for_approval'}),
        revision=1, fencing_token=1, now=time.time())
    store.save(checkpoint, expected_revision=0)


def test_control_run_is_reserved_before_action_and_verified_after_actual_checkpoint(control):
    service, request, store, keys = control
    with service.acquire(request) as lease:
        reserved = runs()
        assert len(reserved) == 1 and reserved[0].state == 'reserved'
        assert reserved[0].dispatch_lease_id == lease.checkpoint.checkpoint_id
        assert reserved[0].assignment_id == lease.checkpoint.state.business_data['owner']
        save_checkpoint(request, store, keys)
    assert runs()[0].state == 'succeeded'
    observed = service.observations[request.run_id]
    assert len(observed) == 1 and observed[0]['reserved_before_execution']
    assert observed[0]['production_release_denied'] and observed[0]['scope'] == 'test'


def test_registry_failure_rolls_back_identity_and_stops_action(control, monkeypatch):
    from agent.services.hub_evidence_registry_service import HubEvidenceRegistryService

    service, request, _, _ = control

    def reject(*args, **kwargs):
        raise RuntimeError('synthetic_registry_unavailable')

    monkeypatch.setattr(HubEvidenceRegistryService, 'reserve_run', reject)
    with pytest.raises(RuntimeError, match='synthetic_registry_unavailable'):
        with service.acquire(request):
            pytest.fail('Control action must not start without reservation')
    assert runs() == []
    with Session(database.engine) as session:
        assert list(session.exec(select(HubSourceEvidenceIdentityDB))) == []
    with service._leases.acquire(request) as lease:
        lease.ensure_valid()  # Failed admission released the actual Hub lease.


def test_changed_input_cannot_complete_original_reserved_control_run(control):
    service, request, store, keys = control
    with pytest.raises(AssertionError, match='full_app_control_input_changed'):
        with service.acquire(request):
            save_checkpoint(request, store, keys)
            request.input_data['changed_after_reservation'] = True
    assert runs()[0].state == 'reserved'
    assert not service.observations


def test_changed_sql_task_scope_cannot_complete_original_control_run(control):
    service, request, store, keys = control
    with pytest.raises(AssertionError, match='full_app_control_evidence_task_scope_changed'):
        with service.acquire(request):
            save_checkpoint(request, store, keys)
            with Session(database.engine) as session, session.begin():
                task = session.get(TaskDB, request.control_task_id)
                task.project_id = None
                session.add(task)
    assert runs()[0].state == 'reserved'
    assert not service.observations


def test_signed_control_payload_is_bound_before_action(control):
    from agent.services.workflow_runtime import SignedWorkflowCommand

    service, request, store, keys = control
    command = SignedWorkflowCommand.issue(key_ring=keys, command_type='approve',
        tenant_id=request.plan.tenant_id, workflow_id=request.plan.workflow_id, run_id=request.run_id,
        step_id='review', checkpoint_id='synthetic-prior-checkpoint', expected_revision=1,
        plan_hash=request.plan.plan_hash, policy_version=request.plan.policy_version,
        actor_id='synthetic-hub-policy', actor_roles=('operator',), payload={'decision': 'approve'})
    with pytest.raises(AssertionError, match='full_app_control_input_changed'):
        with service.acquire(request, command=command):
            save_checkpoint(request, store, keys)
            command.payload['decision'] = 'reject'
    assert runs()[0].state == 'reserved'
    assert not service.observations


def test_refused_control_stays_reserved_without_invented_success(control):
    service, request, _, _ = control
    with pytest.raises(PermissionError, match='synthetic_policy_denial'):
        with service.acquire(request):
            raise PermissionError('synthetic_policy_denial')
    assert runs()[0].state == 'reserved'
    observed = service.observations[request.run_id][0]
    assert observed['state'] == 'reserved' and observed['result_admitted'] is False
    assert observed['reserved_before_execution'] and observed['production_release_denied']
