"""Hub-owned SQL assignment/lease and Registry for the isolated browser executor.

The bootstrap token admits this test executor only. It does not grant productive
Worker registration, model access or release authority.
"""

import os
import re
import time
import uuid
from dataclasses import asdict

from sqlalchemy import text
from sqlmodel import Session, select

from agent.db_models import TaskDB
from agent.db_models.projects import ProjectDB
from agent.db_models.workflow_runtime import WorkflowExecutionOwnershipDB, WorkflowWorkerAssignmentDB
from agent.repositories.evidence_identity_transaction import TransactionEvidenceIdentityRepository
from agent.repositories.sqlalchemy_support import require_locked_row, sqlite_transaction_guard
from agent.services.hub_evidence_registry_service import HubEvidenceRegistryService
from agent.services.workflow_runtime.sqlalchemy_ownership import SQLAlchemyExecutionOwnershipStore
from agent.services.workflow_worker_assignment_service import (
    SQLAlchemyWorkflowWorkerAssignmentStore,
    WorkflowWorkerAssignment,
)
from ananta_contracts.native_context_bundle import native_context_digest


class BrowserEvidenceError(ValueError):
    pass


class SyntheticBrowserEvidence:
    tenant = 'synthetic-browser-tenant'
    project = 'synthetic-browser-project'
    workflow = 'synthetic-browser-acceptance'
    step = 'chromium'
    worker = 'synthetic-browser-executor'
    worker_url = 'http://browser:8080'

    def __init__(self, *, engine, revision, source_digest, image_id, clock=time.time):
        if (not isinstance(revision, str) or re.fullmatch(r'[0-9a-f]{40}', revision) is None
                or not isinstance(source_digest, str) or re.fullmatch(r'[0-9a-f]{64}', source_digest) is None
                or not isinstance(image_id, str) or re.fullmatch(r'sha256:[0-9a-f]{64}', image_id) is None):
            raise BrowserEvidenceError('browser_evidence_source_environment_invalid')
        self.engine, self.clock = engine, clock
        self.revision, self.source_digest, self.image_id = revision, source_digest, image_id
        self.ownership = SQLAlchemyExecutionOwnershipStore(engine)
        self.assignments = SQLAlchemyWorkflowWorkerAssignmentStore(engine)
        self.task_id = 'bpmn-browser-task-' + uuid.uuid4().hex  # A Hub task key, never an evidence ID.
        self.run_key = self.task_id
        self.projection = None

    def reserve(self):
        claim = self.ownership.claim(tenant_id=self.tenant, workflow_id=self.workflow, run_id=self.run_key,
            step_id=self.step, owner_id='synthetic-browser-hub', lease_seconds=120, maximum_retries=0,
            now=self.clock())
        if not claim.acquired:
            raise BrowserEvidenceError('browser_assignment_lease_not_acquired')
        self.lease = claim.ownership
        assignment = self.assignments.bind(WorkflowWorkerAssignment(tenant_id=self.tenant,
            workflow_id=self.workflow, run_id=self.run_key, step_id=self.step,
            attempt_id=self.lease.attempt_id, fencing_token=self.lease.fencing_token,
            hub_task_id=self.task_id, worker_id=self.worker, worker_url=self.worker_url, assigned_at=self.clock()))
        self.assignment = assignment
        self.assignment_id = 'browser-assignment:' + native_context_digest(asdict(assignment))
        self.dispatch_lease_id = 'browser-dispatch:' + native_context_digest(asdict(self.lease))
        context = {'schema': 'ananta.synthetic-browser-input.v1', 'repository_revision': self.revision,
            'source_digest': self.source_digest, 'browser_image_id': self.image_id,
            'purpose': 'authenticated_bpmn_editor_and_command_recovery', 'subject': 'bpmn-owner',
            'workflow_ids': ['browser_authenticated_workflow', 'browser_control_workflow', 'browser_retry_workflow']}
        with sqlite_transaction_guard(self.engine), Session(self.engine) as session, session.begin():
            if self.engine.dialect.name == 'sqlite':
                session.execute(text('BEGIN IMMEDIATE'))
            if session.get(ProjectDB, (self.tenant, self.project)) is None:
                session.add(ProjectDB(tenant_id=self.tenant, project_id=self.project,
                                      name='Synthetic browser acceptance', created_by_subject_id='synthetic-hub'))
                session.flush()
            task = TaskDB(id=self.task_id, tenant_id=self.tenant, project_id=self.project,
                title='Synthetic browser acceptance assignment', task_kind='bpmn_browser_acceptance',
                status='assigned', worker_execution_context=context)
            session.add(task)
            session.flush()
            registry = HubEvidenceRegistryService(TransactionEvidenceIdentityRepository(session), clock=self.clock)
            source = registry.register_source(tenant_id=self.tenant, project_id=self.project,
                origin_type='synthetic_bpmn_browser_source_snapshot', origin_digest=self.source_digest,
                content_digest=native_context_digest(context),
                policy_digest=native_context_digest({'synthetic': True, 'purpose': context['purpose']}),
                evidence_scope='test', synthetic=True)
            run = registry.reserve_run(tenant_id=self.tenant, project_id=self.project, task_id=self.task_id,
                assignment_id=self.assignment_id, dispatch_lease_id=self.dispatch_lease_id,
                repository_revision=self.revision, input_digest=native_context_digest(context),
                execution_profile_digest=native_context_digest({'worker': self.worker, 'image': self.image_id,
                    'headless': True, 'maximum_seconds': 90}),
                environment_digest=native_context_digest({'source_digest': self.source_digest,
                    'database': self.engine.dialect.name, 'network': 'owned_internal', 'synthetic': True}),
                source_ids=(source.source_id,), evidence_scope='test', synthetic=True,
                idempotency_key=self.task_id)
            self.projection = registry.assignment_projection(tenant_id=self.tenant, project_id=self.project,
                run_id=run.run_id, task_id=self.task_id, assignment_id=self.assignment_id,
                dispatch_lease_id=self.dispatch_lease_id, expected_binding_digest=run.binding_digest)
            self.reserved_at = run.created_at_epoch
            task.worker_execution_context = {**context, 'evidence_assignment': self.projection}
            session.add(task)
        return self.projection

    def complete(self, report):
        if type(report.get('passed')) is not bool:
            raise BrowserEvidenceError('browser_evidence_result_state_invalid')
        if not report['passed']:
            raise BrowserEvidenceError('browser_evidence_result_not_verified')
        if self.projection is None or report.get('evidence_assignment') != self.projection:
            raise BrowserEvidenceError('browser_evidence_assignment_mismatch')
        started, finished = report.get('started_at'), report.get('finished_at')
        if (type(started) not in (int, float) or type(finished) not in (int, float)
                or not self.reserved_at <= started <= finished <= self.clock() + 1):
            raise BrowserEvidenceError('browser_evidence_execution_window_invalid')
        digest = native_context_digest(report)
        with sqlite_transaction_guard(self.engine), Session(self.engine) as session, session.begin():
            if self.engine.dialect.name == 'sqlite':
                session.execute(text('BEGIN IMMEDIATE'))
            task = require_locked_row(session, TaskDB, self.task_id, missing_reason='browser_evidence_task_missing')
            owner = session.exec(select(WorkflowExecutionOwnershipDB).where(
                WorkflowExecutionOwnershipDB.tenant_id == self.tenant,
                WorkflowExecutionOwnershipDB.run_id == self.run_key,
                WorkflowExecutionOwnershipDB.step_id == self.step).with_for_update()).one()
            assignment = session.exec(select(WorkflowWorkerAssignmentDB).where(
                WorkflowWorkerAssignmentDB.tenant_id == self.tenant,
                WorkflowWorkerAssignmentDB.run_id == self.run_key,
                WorkflowWorkerAssignmentDB.step_id == self.step).with_for_update()).one()
            if (owner.status != 'active' or owner.lease_expires_at <= self.clock()
                    or owner.attempt_id != self.lease.attempt_id or owner.fencing_token != self.lease.fencing_token
                    or owner.owner_id != self.lease.owner_id or owner.workflow_id != self.workflow
                    or owner.lease_expires_at != self.lease.lease_expires_at
                    or assignment.hub_task_id != self.task_id or assignment.worker_id != self.worker
                    or assignment.worker_url != self.worker_url or assignment.revision != self.assignment.revision
                    or assignment.attempt_id != owner.attempt_id or assignment.fencing_token != owner.fencing_token
                    or assignment.workflow_id != self.workflow
                    or (task.tenant_id, task.project_id) != (self.tenant, self.project)
                    or task.status != 'assigned'
                    or task.worker_execution_context.get('evidence_assignment') != self.projection):
                raise BrowserEvidenceError('browser_evidence_current_assignment_denied')
            registry = HubEvidenceRegistryService(TransactionEvidenceIdentityRepository(session), clock=self.clock)
            stored_run = TransactionEvidenceIdentityRepository(session).get_run(
                tenant_id=self.tenant, project_id=self.project, run_id=self.projection['run_id'])
            input_digest = native_context_digest({key: value for key, value in task.worker_execution_context.items()
                                                 if key != 'evidence_assignment'})
            if stored_run is None or stored_run.input_digest != input_digest:
                raise BrowserEvidenceError('browser_evidence_input_changed')
            if stored_run.state == 'reserved':
                registry.assignment_projection(tenant_id=self.tenant, project_id=self.project,
                    run_id=self.projection['run_id'], task_id=self.task_id, assignment_id=self.assignment_id,
                    dispatch_lease_id=self.dispatch_lease_id, expected_binding_digest=self.projection['binding_digest'],
                    input_digest=input_digest)
            else:
                registry.require_assignment_result(tenant_id=self.tenant, project_id=self.project,
                    projection=self.projection, task_id=self.task_id, assignment_id=self.assignment_id,
                    dispatch_lease_id=self.dispatch_lease_id, result_digest=digest)
            run = registry.record_result(tenant_id=self.tenant, project_id=self.project,
                run_id=self.projection['run_id'], assignment_id=self.assignment_id,
                dispatch_lease_id=self.dispatch_lease_id, terminal_state='succeeded' if report['passed'] else 'failed',
                result_digest=digest)
            registry.require_assignment_result(tenant_id=self.tenant, project_id=self.project,
                projection=self.projection, task_id=self.task_id, assignment_id=self.assignment_id,
                dispatch_lease_id=self.dispatch_lease_id, result_digest=digest)
            denied = registry.verify_release_binding(tenant_id=self.tenant, project_id=self.project,
                run_id=run.run_id, required_scope='production', task_id=self.task_id,
                repository_revision=self.revision, source_ids=run.source_ids)
            if denied.verified:
                raise BrowserEvidenceError('synthetic_browser_production_release_forbidden')
            return {'run_id': run.run_id, 'source_ids': list(run.source_ids), 'scope': 'test',
                'state': run.state, 'reserved_before_execution': True, 'production_release_denied': True,
                'actual_assignment_revision': assignment.revision, 'actual_lease_fencing_token': owner.fencing_token}


def browser_evidence(engine):
    return SyntheticBrowserEvidence(engine=engine, revision=os.environ['BPMN_REPOSITORY_REVISION'],
        source_digest=os.environ['BPMN_REPOSITORY_SOURCE_DIGEST'], image_id=os.environ['BPMN_BROWSER_IMAGE_ID'])
