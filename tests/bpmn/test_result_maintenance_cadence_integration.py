"""Actual durable Hub queue/grants/assignment, signed Worker HTTP and scoped SQLite.

The release/outer Worker-auth policy ports are explicit synthetic fixtures.
This verifies local execution composition, never production release evidence.
"""

import base64
import copy
import sqlite3
import time
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlmodel import Session, SQLModel, create_engine, select

from agent.db_models import AgentInfoDB, TaskDB
from agent.db_models.projects import ProjectDB
from agent.repositories.core import AgentRepository
from agent.repositories.native_maintenance_cadence import SQLMaintenanceCadenceRepository
from agent.repositories.native_result_retirement import SQLNativeResultRetirementRepository
from agent.repositories.tasks import TaskRepository
from agent.services.background.workflow_runtime_reconciler import _maintenance_interval
from agent.services.native_dispatch_journal import SQLAlchemyNativeDispatchJournal
from agent.services.native_graph_orchestration_service import NativeGraphOrchestrator
from agent.services.native_graph_task_queue_adapter import AnantaHubTaskQueueAdapter
from agent.services.native_result_authority import SQLAlchemyNativeResultAuthority
from agent.services.native_result_completion_policy import NativeResultCompletionPolicy
from agent.services.native_result_maintenance_cadence import NativeResultMaintenanceCadence
from agent.services.native_result_maintenance_policy import configured_maintenance_schedules
from agent.services.native_result_maintenance_runtime import (
    MaintenanceControlPolicy,
    NativeMaintenanceRuns,
    RegisteredMaintenanceAdmission,
    run_native_result_maintenance_tick,
)
from agent.services.native_result_maintenance_scope import HubResultMaintenanceScopePreparer
from agent.services.native_result_retirement_runtime import RetirementIssuancePolicy
from agent.services.native_result_retirement_service import NativeResultRetirementService
from agent.services.native_task_ingestion import ingest_native_task_fenced
from agent.services.workflow_authorization_grant_service import SQLAlchemyWorkflowAuthorizationGrantService
from agent.services.workflow_control_persistence import SQLAlchemyWorkflowCommandReplayNonceStore
from agent.services.workflow_control_service import RuntimeSelection
from agent.services.workflow_runtime import (
    AuthorizationVerifier,
    InMemoryReplayNonceStore,
    RuntimeAuthorizationEnvelope,
    WorkflowCommandVerifier,
)
from agent.services.workflow_runtime.sqlalchemy_event_stores import SQLAlchemyCheckpointStore, SQLAlchemyEventStore
from agent.services.workflow_runtime.sqlalchemy_ownership import SQLAlchemyExecutionOwnershipStore
from agent.services.workflow_runtime.sqlalchemy_side_effects import SQLAlchemySideEffectLedger
from agent.services.workflow_worker_assignment_service import (
    SQLAlchemyWorkflowWorkerAssignmentStore,
    WorkflowWorkerAssignmentService,
)
from agent.services.workflow_worker_service_auth import STRICT_WORKER_REGISTRATION_PROVENANCE
from ananta_contracts.native_result_maintenance import MAINTENANCE_CAPABILITY, RESULT_STORE_BINDING_LIMIT
from ananta_contracts.native_result_retirement import RETIREMENT_CAPABILITY
from ananta_contracts.native_task_execution import sign_native_execution_request
from ananta_contracts.runtime_authorization_crypto import Ed25519SigningKeyRing, Ed25519VerificationKeyRing
from tests.bpmn.completion_helpers import SimulatedHubCrash
from tests.bpmn.test_native_task_execution_http import signed as signed  # noqa: F401
from tests.bpmn.test_result_maintenance_cadence import policy
from tests.test_native_graph_runtime import AllowPolicy
from worker.runtime.native_graph.execution_admission import NativeExecutionAdmission
from worker.runtime.native_graph.http_execution import create_native_execution_blueprint
from worker.runtime.native_graph.node_runtime import NativeDelegatedNodeRuntime
from worker.runtime.native_graph.result_maintenance_node import NativeResultMaintenanceHandler
from worker.runtime.native_graph.result_store import SQLiteNativeResultStore
from worker.runtime.native_graph.result_store_runtime import configured_native_result_store_for_task
from worker.runtime.native_graph.task_adapter import NativeGraphWorkerTaskAdapter
from worker.runtime.workflow_adapter_task_consumer import ExecutionAuthorizationDecision, WorkflowAdapterTaskConsumer
from worker.runtime.workflow_adapter_task_execution import consume_delegated_workflow_task


@pytest.fixture
def integrated(tmp_path, monkeypatch, signed):  # noqa: F811 -- explicit imported fixture
    engine = create_engine(f"sqlite:///{tmp_path / 'hub.sqlite'}")
    SQLModel.metadata.create_all(engine)
    now, config = [100.0], [policy()]
    schedule, = configured_maintenance_schedules(config[0])
    owner = schedule.owner
    keys = Ed25519SigningKeyRing({'synthetic': base64.b64encode(b's' * 32).decode()}, active_key_id='synthetic')
    verification_keys = Ed25519VerificationKeyRing.from_mapping(keys.verification_mapping())
    monkeypatch.setattr('agent.repositories.tasks._engine', lambda: engine)
    monkeypatch.setattr('agent.repositories.core.engine', engine)
    monkeypatch.setattr('agent.services.native_result_maintenance_scope._engine', lambda: engine)
    completion = NativeResultCompletionPolicy(authority=SQLAlchemyNativeResultAuthority(key_ring=lambda: keys),
        clock=lambda: now[0])
    tasks, agents = TaskRepository(completion_policy=completion), AgentRepository()
    registry = SimpleNamespace(task_repo=tasks, agent_repo=agents)
    monkeypatch.setattr('agent.services.native_result_maintenance_scope.get_repository_registry', lambda: registry)
    with Session(engine) as session, session.begin():
        session.add(ProjectDB(tenant_id=schedule.tenant_id, project_id=schedule.project_id,
            name='Synthetic cadence project', created_by_subject_id='synthetic-owner'))
        session.add(TaskDB(id=schedule.control_task_id, tenant_id=schedule.tenant_id,
            project_id=schedule.project_id, status='running'))
        session.add(AgentInfoDB(name=owner['worker_id'], url=owner['worker_url'], role='worker', status='online',
            last_seen=100, registration_validated=True, registration_provenance=STRICT_WORKER_REGISTRATION_PROVENANCE,
            authorized_capabilities=['workflow.adapter.native', MAINTENANCE_CAPABILITY, RETIREMENT_CAPABILITY],
            execution_limits={RESULT_STORE_BINDING_LIMIT: owner['store_binding_id']}))

    def ingest(*, lease, **values):
        ingest_native_task_fenced(repository=tasks, source_resolver=lambda fields: None,
            post_commit=lambda *args, **kwargs: None, lease=lease, **values)

    queue = AnantaHubTaskQueueAdapter(task_queue=SimpleNamespace(ingest_task_fenced=ingest),
        task_repository=tasks, task_runtime=SimpleNamespace(notify_native_task_cancelled=lambda *args, **kwargs: None),
        maintenance_scope_preparer=HubResultMaintenanceScopePreparer(clock=lambda: now[0]))
    grants = SQLAlchemyWorkflowAuthorizationGrantService(engine, clock=lambda: now[0])
    ledger, ownership = SQLAlchemySideEffectLedger(engine), SQLAlchemyExecutionOwnershipStore(engine)
    checkpoints = SQLAlchemyCheckpointStore(engine)
    reservations = SQLMaintenanceCadenceRepository(engine)
    release = SimpleNamespace(allowed=True)

    class SyntheticRelease:
        def evaluate(self, **values):
            return release.allowed, 'synthetic_fixture_admission'

    admission = RegisteredMaintenanceAdmission(engine=engine, registry=lambda: registry,
        release=SyntheticRelease(), clock=lambda: now[0])

    def runtime():
        return NativeGraphOrchestrator(queue=queue, checkpoints=checkpoints, events=SQLAlchemyEventStore(engine),
            ownership=ownership, ledger=ledger, key_ring=keys, clock=lambda: now[0],
            command_verifier=WorkflowCommandVerifier(
                keys, SQLAlchemyWorkflowCommandReplayNonceStore(engine, clock=lambda: now[0])),
            policy=MaintenanceControlPolicy(configuration=lambda: config[0], admission=admission,
                reservations=reservations),
            authorization_grants=grants)

    def cadence():
        def retire_unstarted(reservation, *, lease):
            reservations.finish(reservation, status='cancelled', now=now[0], lease=lease)

        return NativeResultMaintenanceCadence(store=SQLMaintenanceCadenceRepository(engine),
            runs=NativeMaintenanceRuns(runtime_factory=runtime, checkpoints=checkpoints,
                keys=lambda: keys, clock=lambda: now[0], retire_unstarted=retire_unstarted), admission=admission,
            configuration=lambda: config[0], clock=lambda: now[0])

    private = tmp_path / 'worker-private'
    private.mkdir(mode=0o700)
    store = SQLiteNativeResultStore(private / 'results.sqlite3', clock=lambda: now[0],
        retention_seconds=1, expire_on_open=False, max_records=2,
        store_binding_id=owner['store_binding_id'])
    old = copy.deepcopy(signed[0]['task'])
    old.update(tenant_id=schedule.tenant_id, project_id=schedule.project_id)
    old['worker_execution_context']['native_node_command']['tenant_id'] = schedule.tenant_id
    foreign = copy.deepcopy(old)
    foreign.update(id='foreign-project-slot', project_id='foreign-project')
    for task in (old, foreign):
        store.reserve(task)
        store.publish(task, {'status': 'completed', 'output': 'synthetic-private-result-canary'})
    now[0] = 102
    snapshot = SimpleNamespace(task=None)

    class Scope:
        def task_snapshot(self, *, hub_task_id):
            assert snapshot.task['id'] == hub_task_id
            return snapshot.task

        def revalidate(self, envelope):
            return grants.revalidate(envelope)

    worker_runtime = NativeDelegatedNodeRuntime(
        handler=NativeResultMaintenanceHandler(scope=Scope(), store_binding_id=owner['store_binding_id'],
            retirement_keys=lambda: verification_keys, clock=lambda: now[0]),
        authorization_verifier=AuthorizationVerifier(
            verification_keys, InMemoryReplayNonceStore(clock=lambda: now[0])),
        policy=AllowPolicy(), capabilities=frozenset({MAINTENANCE_CAPABILITY, RETIREMENT_CAPABILITY}), ledger=ledger,
        hub_revalidator=grants, clock=lambda: now[0])
    outer_authorization = SimpleNamespace(authorize=lambda **kwargs: ExecutionAuthorizationDecision(
        grants.revalidate(RuntimeAuthorizationEnvelope.from_mapping(kwargs['binding'].authorization_envelope)),
        'synthetic_outer_policy_actual_grant'))
    consumer = WorkflowAdapterTaskConsumer(native_adapter=NativeGraphWorkerTaskAdapter(worker_runtime),
        authorization=outer_authorization)
    reservation_admission = NativeExecutionAdmission(authorization=outer_authorization)
    app = Flask(__name__)
    app.config.update(ROLE='worker', ANANTA_NATIVE_RESULT_STORE_PATH=str(store.path))
    app.extensions.update(native_result_store=store, native_result_store_binding=owner['store_binding_id'],
        workflow_adapter_worker_registration={'execution_limits': {
            RESULT_STORE_BINDING_LIMIT: owner['store_binding_id']}})
    app.extensions['workflow_adapter_task_consumer'] = consumer

    def consume(task):
        snapshot.task = task
        return consume_delegated_workflow_task(task)

    app.register_blueprint(create_native_execution_blueprint(token_provider=lambda: 'x' * 40,
        recipient_provider=lambda: (owner['worker_id'], owner['worker_url']),
        verification_provider=lambda: verification_keys, consumer=consume,
        result_store_resolver=configured_native_result_store_for_task,
        execution_admission=reservation_admission.authorize))
    assignment = WorkflowWorkerAssignmentService(ownership=ownership,
        assignments=SQLAlchemyWorkflowWorkerAssignmentStore(engine), clock=lambda: now[0])
    journal = SQLAlchemyNativeDispatchJournal(engine, clock=lambda: now[0])
    retirement = NativeResultRetirementService(repository=SQLNativeResultRetirementRepository(engine),
        authority=SQLAlchemyNativeResultAuthority(key_ring=lambda: keys,
            required_capabilities=(RETIREMENT_CAPABILITY,)),
        keys=lambda: keys, clock=lambda: now[0], policy=RetirementIssuancePolicy(
            configuration=lambda: config[0], admission=admission, reservations=reservations))

    def children():
        with Session(engine) as session:
            return list(session.exec(select(TaskDB).where(TaskDB.parent_task_id == schedule.control_task_id)).all())

    def execute(task_id=None, *, confirm_retirement=True):
        child, = [task for task in children() if task_id is None or task.id == task_id]
        assigned = assignment.bind_dispatched_task(task=child, worker=agents.get_by_url(owner['worker_url']))
        child.status = 'in_progress'
        tasks.save(child)
        retire = child.worker_execution_context['native_node_command']['input_data']['workflow_input'].get(
            'retire_expired') is True
        if retire:
            retirement.prepare(task_id=child.id, assignment=assigned)
            child = tasks.get_by_id(child.id)
        claim = journal.begin(task=child, assignment=assigned)
        value = sign_native_execution_request(child.model_dump(mode='json'), key_ring=keys,
            worker_id=owner['worker_id'], worker_url=owner['worker_url'], expires_at=time.time() + 30)
        response = app.test_client().post('/api/internal/native-tasks/execute', json=value,
            headers={'Authorization': 'Bearer ' + 'x' * 40})
        assert response.status_code == 200, response.json
        result = response.json
        assert result['status'] == 'completed', result
        outer = result['workflow_adapter_verification']['workflow_adapter_task_result']
        candidate = tasks.get_by_id(child.id)
        candidate.status, candidate.last_output, candidate.last_exit_code = 'completed', result['output'], 0
        candidate.verification_status = {'workflow_adapter_task_result': outer,
            'native_node_result': outer['adapter_result']['verification']['native_node_result']}
        accepted = tasks.save(candidate)
        journal.settle(task=accepted, claim=claim, accepted=True)
        if retire and confirm_retirement:
            retirement.confirm(task_id=child.id)
        return accepted

    yield SimpleNamespace(engine=engine, now=now, config=config, schedule=schedule, queue=queue,
        admission=admission, release=release, cadence=cadence, children=children, execute=execute,
        store=store, old=old, foreign=foreign, checkpoints=checkpoints,
        app=app, keys=keys, grants=grants, assignment=assignment, agents=agents,
        retirement=retirement, tasks=tasks, journal=journal)
    engine.dispose()


def test_real_queue_signed_http_expiry_and_hub_receipt_complete_one_reserved_run(integrated):
    assert integrated.cadence().run_once()['active'] == 1
    first, = integrated.children()
    assert first.assigned_agent_url == integrated.schedule.owner['worker_url']
    assert integrated.cadence().run_once()['active'] == 1 and len(integrated.children()) == 1
    accepted = integrated.execute()
    assert accepted.verification_status['native_result_receipt']['classification'] == 'technical_observation'
    assert integrated.cadence().run_once()['completed'] == 1
    assert integrated.cadence().run_once()['reserved'] == 0 and len(integrated.children()) == 1
    with sqlite3.connect(integrated.store.path) as db:
        assert db.execute('SELECT state, result_json FROM native_results WHERE task_id=?',
                          (integrated.old['id'],)).fetchone() == ('expired', None)
        state, payload = db.execute('SELECT state, result_json FROM native_results WHERE task_id=?',
                                    (integrated.foreign['id'],)).fetchone()
        assert state == 'ready' and 'synthetic-private-result-canary' in payload
        assert db.execute('SELECT task_id FROM native_result_maintenance_reservations').fetchone() == (first.id,)


def test_revoked_actual_grant_cannot_consume_capacity_even_with_valid_transport_signature(integrated):
    assert integrated.cadence().run_once()['active'] == 1
    child, = integrated.children()
    owner = integrated.schedule.owner
    integrated.assignment.bind_dispatched_task(task=child,
        worker=integrated.agents.get_by_url(owner['worker_url']))
    command = child.worker_execution_context['native_node_command']
    integrated.grants.revoke(command['authorization']['envelope_id'], reason_code='synthetic_policy_withdrawal')
    payload = sign_native_execution_request(child.model_dump(mode='json'), key_ring=integrated.keys,
        worker_id=owner['worker_id'], worker_url=owner['worker_url'], expires_at=time.time() + 30)
    response = integrated.app.test_client().post('/api/internal/native-tasks/execute', json=payload,
        headers={'Authorization': 'Bearer ' + 'x' * 40})
    assert response.status_code == 409 and response.json['reason_code'] == 'native_execution_authority_denied'
    with sqlite3.connect(integrated.store.path) as db:
        assert db.execute('SELECT count(*) FROM native_results').fetchone()[0] == 2
        assert db.execute('SELECT count(*) FROM native_result_maintenance_reservations').fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM native_results WHERE state='ready'").fetchone()[0] == 2


def test_policy_withdrawal_cancels_actual_queued_task_without_worker_execution(integrated):
    integrated.cadence().run_once()
    integrated.config[0]['enabled'] = False
    service = integrated.cadence()
    result = service.run_once()
    if result['cancelled'] != 1:
        service._runs.cancel(service._store.pending()[0])  # Bound diagnostic if composition unexpectedly blocks.
    assert result['cancelled'] == 1
    child, = integrated.children()
    assert child.status == 'cancelled'
    with sqlite3.connect(integrated.store.path) as db:
        state, payload = db.execute('SELECT state, result_json FROM native_results WHERE task_id=?',
                                    (integrated.old['id'],)).fetchone()
        assert state == 'ready' and 'synthetic-private-result-canary' in payload


def test_missing_runtime_release_admission_creates_no_run_or_task(integrated):
    integrated.release.allowed = False
    assert integrated.cadence().run_once()['blocked'] == 1 and integrated.children() == []


def test_explicit_auto_approval_never_bypasses_mandatory_human_tool_policy(integrated):
    integrated.admission._governance = lambda: {'approval_lifecycle': {
        'human_required_tools': ['native.result.expire']}}
    assert integrated.cadence().run_once()['blocked'] == 1 and integrated.children() == []


@pytest.mark.parametrize('mode,runtime_id', [('blocked', ''), ('shadow', 'ananta-native'), ('live', 'temporal')])
def test_runtime_selection_or_rollout_denial_cannot_be_bypassed_by_auto_approval(integrated, mode, runtime_id):
    integrated.admission._selection = SimpleNamespace(select=lambda **kwargs:
        RuntimeSelection(runtime_id=runtime_id, mode=mode, capabilities=frozenset(), reason_code='synthetic_denial'))
    assert integrated.cadence().run_once()['blocked'] == 1 and integrated.children() == []


def test_rollout_scope_is_bound_to_actual_schedule_tenant_and_project(integrated):
    from agent.services.native_result_maintenance_policy import maintenance_request
    from agent.services.workflow_runtime_rollout_constraints import rollout_scope_from_plan

    scope = rollout_scope_from_plan(maintenance_request(integrated.schedule, 'synthetic-preview').plan)
    assert scope.tenant_id == integrated.schedule.tenant_id and scope.project_id == integrated.schedule.project_id


def test_crash_after_actual_queue_commit_recovers_same_run_and_one_child(integrated, monkeypatch):
    submit = integrated.queue.submit_fenced
    crashed = [False]

    def crash_after_commit(command, *, lease):
        receipt = submit(command, lease=lease)
        if not crashed[0]:
            crashed[0] = True
            raise SimulatedHubCrash('synthetic crash after durable Task commit')
        return receipt

    monkeypatch.setattr(integrated.queue, 'submit_fenced', crash_after_commit)
    with pytest.raises(SimulatedHubCrash):
        integrated.cadence().run_once()
    child, = integrated.children()
    result = integrated.cadence().run_once()
    assert result['active'] == 1 and [item.id for item in integrated.children()] == [child.id]
    assert integrated.execute().status == 'completed'
    assert integrated.cadence().run_once()['completed'] == 1


def test_completed_run_wins_against_later_policy_withdrawal(integrated):
    integrated.cadence().run_once()
    integrated.execute()
    reservation, = SQLMaintenanceCadenceRepository(integrated.engine).pending()
    service = integrated.cadence()
    assert service._runs.advance(reservation).status == 'completed'
    integrated.config[0]['enabled'] = False
    result = integrated.cadence().run_once()
    assert result['completed'] == 1 and result['cancelled'] == 0
    child, = integrated.children()
    assert child.status == 'completed'


def test_worker_tick_never_composes_hub_database_or_queue():
    app = Flask(__name__)
    app.config['ROLE'] = 'worker'
    result = run_native_result_maintenance_tick(app)
    assert result['status'] == 'disabled' and app.extensions == {}


def test_silent_worker_run_has_a_signed_deadline_and_cancels_queued_work(integrated):
    integrated.cadence().run_once()
    integrated.now[0] += 121
    result = integrated.cadence().run_once()
    assert result['failed'] == 1 and result['pending'] == 0
    child, = integrated.children()
    assert child.status == 'cancelled'


def test_background_polling_stays_inside_worker_lease_without_creating_a_new_thread():
    app = Flask(__name__)
    app.config['AGENT_CONFIG'] = {'native_result_maintenance': policy()}
    assert _maintenance_interval(app, 60, {}) == 5
    app.config['AGENT_CONFIG']['native_result_maintenance']['enabled'] = False
    assert _maintenance_interval(app, 60, {'pending': 1}) == 5
    assert _maintenance_interval(app, 60, {'pending': 0}) == 60


def test_retired_before_start_reservation_cannot_authorize_a_late_stale_starter(integrated):
    schedule, = configured_maintenance_schedules(integrated.config[0])
    store = SQLMaintenanceCadenceRepository(integrated.engine)
    reservation = store.reserve(schedule, now=integrated.now[0])
    runs = integrated.cadence()._runs
    assert runs.cancel(reservation) == 'cancelled'
    assert store.pending() == ()
    # This represents an older Hub invocation that already read the reservation.
    result = runs.advance(reservation)
    assert result.status == 'failed' and integrated.children() == []
