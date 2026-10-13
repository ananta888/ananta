"""Synthetic Hub Registry reservations under actual signed control leases."""

import os
import time
from contextlib import contextmanager

from sqlalchemy import text
from sqlmodel import Session

from agent.db_models import TaskDB
from agent.db_models.projects import ProjectDB
from agent.repositories.evidence_identity_transaction import TransactionEvidenceIdentityRepository
from agent.repositories.sqlalchemy_support import require_locked_row, sqlite_transaction_guard
from agent.services.bpmn_run_lease import BpmnRunLeaseService
from agent.services.hub_evidence_registry_service import HubEvidenceRegistryService
from ananta_contracts.hub_evidence import build_hub_evidence_assignment
from ananta_contracts.native_context_bundle import native_context_digest


class SyntheticControlEvidence:
    """Decorate real lease acquisition; never replace execution or lease checks."""

    def __init__(self, *, engine, checkpoints, keys):
        self._engine, self._checkpoints, self._keys = engine, checkpoints, keys
        self._leases = BpmnRunLeaseService(checkpoints=checkpoints, keys=keys, clock=time.time)
        self._revision = os.environ['BPMN_REPOSITORY_REVISION']
        self._source_snapshot = os.environ['BPMN_REPOSITORY_SOURCE_DIGEST']
        self.observations = {}

    @staticmethod
    def _input(request, command):
        return {'plan': request.plan.to_dict(), 'input_data': request.input_data,
                'control_task_id': request.control_task_id, 'run_id': request.run_id,
                'tenant_parallel_limit': request.tenant_parallel_limit,
                'worker_parallel_limit': request.worker_parallel_limit,
                'signed_control_command': command.to_dict() if command is not None else None}

    @contextmanager
    def acquire(self, request, *, command=None):
        with self._leases.acquire(request, command=command) as lease:
            assert lease is not None, 'full_app_control_evidence_requires_actual_lease'
            input_digest = native_context_digest(self._input(request, command))
            project_id, run = self._reserve(request, lease, input_digest)
            started_at = time.time()
            assert run.created_at_epoch <= started_at, 'full_app_control_evidence_post_execution_reservation'
            try:
                yield lease
            except Exception:
                self._observe_pending(request, project_id, run)
                raise
            lease.ensure_valid()
            assert native_context_digest(self._input(request, command)) == input_digest, (
                'full_app_control_input_changed')
            checkpoint = self._checkpoints.get_latest(tenant_id=request.plan.tenant_id,
                run_id=request.run_id, task_id=request.control_task_id)
            assert checkpoint is not None, 'full_app_control_evidence_checkpoint_missing'
            checkpoint.verify(key_ring=self._keys, tenant_id=request.plan.tenant_id,
                workflow_id=request.plan.workflow_id, run_id=request.run_id, task_id=request.control_task_id,
                plan_hash=request.plan.plan_hash, policy_version=request.plan.policy_version)
            self._complete(request, project_id, run, native_context_digest(checkpoint.to_dict()))

    def _reserve(self, request, lease, input_digest):
        lease.ensure_valid()
        with sqlite_transaction_guard(self._engine), Session(self._engine) as session, session.begin():
            if self._engine.dialect.name == 'sqlite':
                session.execute(text('BEGIN IMMEDIATE'))
            task = require_locked_row(session, TaskDB, request.control_task_id,
                                      missing_reason='full_app_control_evidence_task_missing')
            project = session.get(ProjectDB, (task.tenant_id, task.project_id)) if task.project_id else None
            assert task.tenant_id == request.plan.tenant_id and project is not None and project.status == 'active', (
                'full_app_control_evidence_scope_mismatch')
            registry = HubEvidenceRegistryService(TransactionEvidenceIdentityRepository(session))
            source = registry.register_source(tenant_id=task.tenant_id, project_id=task.project_id,
                origin_type='synthetic_bpmn_hub_control_input', origin_digest=native_context_digest({
                    'repository_revision': self._revision, 'source_snapshot': self._source_snapshot,
                    'control_task_id': task.id, 'workflow_id': request.plan.workflow_id}),
                content_digest=input_digest,
                policy_digest=native_context_digest({'policy_version': request.plan.policy_version,
                    'plan_hash': request.plan.plan_hash, 'synthetic': True}), evidence_scope='test', synthetic=True)
            run = registry.reserve_run(tenant_id=task.tenant_id, project_id=task.project_id, task_id=task.id,
                assignment_id=lease.checkpoint.state.business_data['owner'],
                dispatch_lease_id=lease.checkpoint.checkpoint_id, repository_revision=self._revision,
                input_digest=input_digest, execution_profile_digest=native_context_digest({
                    'runtime': 'hub_bpmn_control', 'fencing_token': lease.fencing_token}),
                environment_digest=native_context_digest({'source_snapshot': self._source_snapshot,
                    'database': self._engine.dialect.name, 'synthetic': True}),
                source_ids=(source.source_id,), evidence_scope='test', synthetic=True,
                idempotency_key='bpmn-control:' + lease.checkpoint.checkpoint_id)
            lease.ensure_valid()
            return task.project_id, run

    def _complete(self, request, project_id, run, result_digest):
        with Session(self._engine) as session, session.begin():
            task = require_locked_row(session, TaskDB, request.control_task_id,
                                      missing_reason='full_app_control_evidence_task_missing')
            assert (task.tenant_id, task.project_id) == (request.plan.tenant_id, project_id), (
                'full_app_control_evidence_task_scope_changed')
            registry = HubEvidenceRegistryService(TransactionEvidenceIdentityRepository(session))
            completed = registry.record_result(tenant_id=request.plan.tenant_id, project_id=project_id,
                run_id=run.run_id, assignment_id=run.assignment_id, dispatch_lease_id=run.dispatch_lease_id,
                terminal_state='succeeded', result_digest=result_digest)
            projection = build_hub_evidence_assignment(run_id=run.run_id, task_id=run.task_id,
                assignment_id=run.assignment_id, dispatch_lease_id=run.dispatch_lease_id,
                source_ids=run.source_ids, evidence_scope=run.evidence_scope, binding_digest=run.binding_digest)
            registry.require_assignment_result(tenant_id=request.plan.tenant_id, project_id=project_id,
                projection=projection, task_id=request.control_task_id, assignment_id=run.assignment_id,
                dispatch_lease_id=run.dispatch_lease_id, result_digest=result_digest)
            denied = registry.verify_release_binding(tenant_id=request.plan.tenant_id, project_id=project_id,
                run_id=run.run_id, required_scope='production', task_id=request.control_task_id,
                repository_revision=self._revision, source_ids=run.source_ids)
            assert not denied.verified and denied.reason_code == 'evidence_run_test_scope_forbidden'
            self.observations.setdefault(request.run_id, []).append({'run_id': completed.run_id,
                'scope': completed.evidence_scope, 'state': 'succeeded', 'reserved_before_execution': True,
                'production_release_denied': True, 'actual_control_lease_id': run.dispatch_lease_id})

    def _observe_pending(self, request, project_id, run):
        """An interrupted/refused action is never relabeled as a completed result."""
        with Session(self._engine) as session, session.begin():
            registry = HubEvidenceRegistryService(TransactionEvidenceIdentityRepository(session))
            registry.assignment_projection(tenant_id=request.plan.tenant_id, project_id=project_id,
                run_id=run.run_id, task_id=run.task_id, assignment_id=run.assignment_id,
                dispatch_lease_id=run.dispatch_lease_id, input_digest=run.input_digest,
                expected_binding_digest=run.binding_digest)
            denied = registry.verify_release_binding(tenant_id=request.plan.tenant_id, project_id=project_id,
                run_id=run.run_id, required_scope='production', task_id=run.task_id,
                repository_revision=self._revision, source_ids=run.source_ids)
            assert not denied.verified and denied.reason_code == 'evidence_run_not_successful'
            self.observations.setdefault(request.run_id, []).append({'run_id': run.run_id, 'scope': run.evidence_scope,
                'state': 'reserved', 'reserved_before_execution': True, 'result_admitted': False,
                'production_release_denied': True, 'actual_control_lease_id': run.dispatch_lease_id})
