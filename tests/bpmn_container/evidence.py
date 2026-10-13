"""Actual Hub Registry reservation for synthetic full-application Worker Tasks."""

import os
import time

from sqlalchemy import text
from sqlmodel import Session

from agent.db_models import TaskDB
from agent.db_models.projects import ProjectDB
from agent.repositories.evidence_identity_transaction import TransactionEvidenceIdentityRepository
from agent.repositories.sqlalchemy_support import require_locked_row, sqlite_transaction_guard
from agent.services.hub_evidence_registry_service import HubEvidenceRegistryService
from agent.services.native_artifact_dispatch import native_dispatch_binding_digest
from agent.services.native_control_task_scope import native_control_task_scope
from agent.services.native_result_authority import SQLAlchemyNativeResultAuthority
from agent.services.native_result_task_projection import native_task_command, require_native_result_receipt
from ananta_contracts.hub_evidence import build_hub_evidence_assignment
from ananta_contracts.native_context_bundle import native_context_digest

EVIDENCE_FIELD = 'synthetic_hub_evidence_assignment'


class SyntheticTaskEvidence:
    """Hub-only fixture; real Registry and SQL authority, no invented SRC/RUN IDs."""

    def __init__(self, *, engine, keys):
        self._engine, self._keys = engine, keys
        self._authority = SQLAlchemyNativeResultAuthority(key_ring=keys)
        self._revision = os.environ['BPMN_REPOSITORY_REVISION']
        self._source_snapshot = os.environ['BPMN_REPOSITORY_SOURCE_DIGEST']

    def prepare(self, *, task_id, assignment):
        with sqlite_transaction_guard(self._engine), Session(self._engine) as session, session.begin():
            if self._engine.dialect.name == 'sqlite':
                session.execute(text('BEGIN IMMEDIATE'))
            task = require_locked_row(session, TaskDB, task_id, missing_reason='test_evidence_task_missing')
            command = native_task_command(task)
            parent = require_locked_row(session, TaskDB, command.control_task_id,
                                        missing_reason='test_evidence_control_task_missing')
            native_control_task_scope(parent.model_dump(), command=command)
            assert parent.tenant_id == task.tenant_id and parent.project_id, 'test_evidence_control_scope_missing'
            project = session.get(ProjectDB, (parent.tenant_id, parent.project_id))
            assert project is not None and project.status == 'active', 'test_evidence_project_not_active'
            assert task.project_id in (None, parent.project_id), 'test_evidence_project_rebinding_denied'
            # Control ownership is carried by the closed Native command. The
            # generic parent_task_id denotes an execution dependency instead.
            task.project_id = parent.project_id
            current = self._authority.require_current(session=session, task=task, command=command, now=time.time())
            assert (current.worker_id, current.worker_url, current.assignment_revision) == (
                assignment.worker_id, assignment.worker_url, assignment.revision), 'test_evidence_assignment_mismatch'
            assert task.status in {'created', 'assigned', 'queued'}, 'test_evidence_post_execution_reservation'
            assert EVIDENCE_FIELD not in task.worker_execution_context, 'test_evidence_duplicate_preparation'
            registry = HubEvidenceRegistryService(TransactionEvidenceIdentityRepository(session))
            digest = native_context_digest(task.worker_execution_context)
            policy = native_context_digest({'policy_version': command.policy_version, 'plan_hash': command.plan_hash,
                'scope': 'test', 'synthetic': True})
            source = registry.register_source(tenant_id=task.tenant_id, project_id=task.project_id,
                origin_type='synthetic_compiled_bpmn_worker_task', origin_digest=native_context_digest({
                    'task_id': task.id, 'workflow_id': command.workflow_id, 'plan_hash': command.plan_hash,
                    'repository_revision': self._revision, 'source_snapshot': self._source_snapshot}),
                content_digest=digest, policy_digest=policy, evidence_scope='test', synthetic=True)
            dispatch = 'native-dispatch:' + native_dispatch_binding_digest(command=command, authority=current)
            run = registry.reserve_run(tenant_id=task.tenant_id, project_id=task.project_id, task_id=task.id,
                assignment_id=current.assignment_id, dispatch_lease_id=dispatch, repository_revision=self._revision,
                input_digest=digest, execution_profile_digest=native_context_digest({
                    'runtime': 'native', 'worker_id': current.worker_id, 'worker_url': current.worker_url,
                    'assignment_revision': current.assignment_revision, 'synthetic': True}),
                environment_digest=native_context_digest({'source_snapshot': self._source_snapshot,
                    'isolation': 'separate_owned_read_only_containers', 'external_llm': False}),
                source_ids=(source.source_id,), evidence_scope='test', synthetic=True,
                idempotency_key='bpmn-container:' + task.id)
            projection = build_hub_evidence_assignment(run_id=run.run_id, task_id=task.id,
                assignment_id=run.assignment_id, dispatch_lease_id=run.dispatch_lease_id,
                source_ids=run.source_ids, evidence_scope=run.evidence_scope, binding_digest=run.binding_digest)
            task.worker_execution_context = {**task.worker_execution_context, EVIDENCE_FIELD: projection}
            session.add(task)

    def complete(self, task):
        command = native_task_command(task)
        require_native_result_receipt(task, command)
        projection = task.worker_execution_context[EVIDENCE_FIELD]
        observed = task.verification_status['native_node_result']['output_data'].get('hub_test_evidence_assignment')
        assert observed == projection, 'test_evidence_worker_projection_changed'
        with Session(self._engine) as session, session.begin():
            registry = HubEvidenceRegistryService(TransactionEvidenceIdentityRepository(session))
            digest = native_context_digest(task.verification_status['native_node_result'])
            run = registry.record_result(tenant_id=task.tenant_id, project_id=task.project_id,
                run_id=projection['run_id'], assignment_id=projection['assignment_id'],
                dispatch_lease_id=projection['dispatch_lease_id'], terminal_state='succeeded', result_digest=digest)
            output = task.verification_status['native_node_result']['output_data']
            assert run.created_at_epoch <= output['started_at'] <= output['finished_at'], (
                'test_evidence_reservation_not_before_execution')
            registry.require_assignment_result(tenant_id=task.tenant_id, project_id=task.project_id,
                projection=projection,
                task_id=task.id, assignment_id=projection['assignment_id'],
                dispatch_lease_id=projection['dispatch_lease_id'], result_digest=digest)
            denied = registry.verify_release_binding(tenant_id=task.tenant_id, project_id=task.project_id,
                run_id=run.run_id, required_scope='production', task_id=task.id,
                repository_revision=self._revision, source_ids=run.source_ids)
            assert not denied.verified and denied.reason_code == 'evidence_run_test_scope_forbidden'
            return {'run_id': run.run_id, 'source_ids': list(run.source_ids), 'scope': run.evidence_scope,
                    'reserved_before_execution': True, 'production_release_denied': True}

    def pending(self, task):
        """Verify original reservation without manufacturing a terminal Worker result."""
        assert not task.verification_status.get('native_result_receipt'), 'test_evidence_pending_has_receipt'
        projection = task.worker_execution_context[EVIDENCE_FIELD]
        with Session(self._engine) as session, session.begin():
            registry = HubEvidenceRegistryService(TransactionEvidenceIdentityRepository(session))
            current = registry.assignment_projection(tenant_id=task.tenant_id, project_id=task.project_id,
                run_id=projection['run_id'], task_id=task.id, assignment_id=projection['assignment_id'],
                dispatch_lease_id=projection['dispatch_lease_id'], expected_binding_digest=projection['binding_digest'])
            assert current == projection, 'test_evidence_pending_assignment_changed'
            denied = registry.verify_release_binding(tenant_id=task.tenant_id, project_id=task.project_id,
                run_id=projection['run_id'], required_scope='production', task_id=task.id,
                repository_revision=self._revision, source_ids=tuple(projection['source_ids']))
            assert not denied.verified and denied.reason_code == 'evidence_run_not_successful'
            return {'run_id': projection['run_id'], 'scope': projection['evidence_scope'], 'state': 'reserved',
                    'reserved_before_dispatch': True, 'result_admitted': False, 'production_release_denied': True}
