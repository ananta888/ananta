"""Hub-owned atomic Pi reservation and signed send preparation; never sends."""

import time
from dataclasses import dataclass

from sqlmodel import Session

from agent.db_models import TaskDB
from agent.repositories.evidence_identity_transaction import TransactionEvidenceIdentityRepository
from agent.repositories.pi_dispatch_evidence import TransactionPiDispatchEvidenceRepository
from agent.repositories.sqlalchemy_support import require_locked_row, sqlite_transaction_guard
from agent.services.hub_evidence_registry_service import HubEvidenceRegistryService
from agent.services.native_artifact_dispatch import native_dispatch_binding_digest
from agent.services.native_dispatch_journal import SQLAlchemyNativeDispatchJournal
from agent.services.pi_context_evidence import PiContextEvidenceAdmission
from agent.services.pi_result_task_projection import pi_task_command
from agent.services.pi_run_evidence_completion import PiRunEvidenceCompletionService
from agent.services.pi_run_evidence_reservation import PiRunEvidenceReservationService
from agent.services.pi_runtime_manifest_admission import PiRuntimeManifestAdmissionService
from ananta_contracts.native_context_bundle import native_context_digest
from ananta_contracts.native_task_execution import sign_native_execution_request


@dataclass(frozen=True)
class PreparedPiDispatch:
    claim: str
    payload: dict


class PiDispatchPreparationService:
    """Independent deployment/policy ports remain exclusively Hub-owned."""

    def __init__(self, *, engine, deployments, authority, policy_digest, signing_keys, clock=time.time, contexts=None):
        self._engine, self._deployments, self._authority = engine, deployments, authority
        self._policy_digest, self._signing_keys, self._clock = policy_digest, signing_keys, clock
        self._contexts = contexts

    def prepare(self, *, task_id, assignment):
        with sqlite_transaction_guard(self._engine), Session(self._engine) as session, session.begin():
            task = require_locked_row(session, TaskDB, task_id, missing_reason='pi_evidence_task_missing')
            command = pi_task_command(task)
            if task.status not in {'created', 'assigned', 'queued'}:
                raise ValueError('pi_evidence_pre_execution_task_required')
            current = self._authority.require_current(session=session, task=task, command=command, now=self._clock())
            if (assignment.worker_id != current.worker_id or assignment.worker_url != current.worker_url
                    or type(assignment.revision) is not int or assignment.revision != current.assignment_revision):
                raise ValueError('pi_dispatch_current_assignment_required')
            manifest = self._deployments.require_manifest(tenant_id=task.tenant_id, project_id=task.project_id,
                worker_id=current.worker_id, worker_url=current.worker_url)
            registry = HubEvidenceRegistryService(TransactionEvidenceIdentityRepository(session), clock=self._clock)
            manifests = PiRuntimeManifestAdmissionService(deployments=self._deployments, registry=registry)
            policy_digest = self._policy_digest(task)
            admitted = manifests.admit(manifest=manifest, policy_digest=policy_digest)
            reservation = PiRunEvidenceReservationService(manifests=manifests, registry=registry,
                authority=self._authority, clock=self._clock, contexts=self._contexts)
            values = dict(task_id=task.id, manifest=manifest, source_id=admitted.source.source_id,
                source_binding_digest=admitted.source.binding_digest, policy_digest=policy_digest)
            run = reservation.reserve(session=session, **values)
            projection = reservation.assignment_projection(session=session, run_id=run.run_id,
                expected_binding_digest=run.binding_digest, **values)
            payload = sign_native_execution_request(task.model_dump(mode='json'), key_ring=self._signing_keys(),
                worker_id=current.worker_id, worker_url=current.worker_url,
                expires_at=min(self._clock() + 30, command.authorization.expires_at), evidence_assignment=projection)
            claim = SQLAlchemyNativeDispatchJournal(self._engine, clock=self._clock).begin_in_transaction(
                session=session, task=task, assignment=assignment)
            TransactionPiDispatchEvidenceRepository(session).add(task=task, claim=claim, projection=projection)
            task.status = 'in_progress'
            session.flush()
            prepared = PreparedPiDispatch(claim=claim, payload=payload)
        # A failed commit never returns a payload to the transport caller.
        return prepared

    def complete(self, *, task_id, assignment):
        """Publish only the exact independently admitted Hub terminal receipt."""
        with sqlite_transaction_guard(self._engine), Session(self._engine) as session, session.begin():
            registry = HubEvidenceRegistryService(TransactionEvidenceIdentityRepository(session), clock=self._clock)
            return PiRunEvidenceCompletionService(registry=registry, contexts=self._contexts).complete(
                session=session, task_id=task_id, assignment=assignment)

    def complete_reserved(self, *, task_id):
        """Recover the persisted assignment and complete it without a new RUN."""
        with sqlite_transaction_guard(self._engine), Session(self._engine) as session, session.begin():
            task = require_locked_row(session, TaskDB, task_id, missing_reason='pi_evidence_task_missing')
            projection = TransactionPiDispatchEvidenceRepository(session).require(task=task)
            registry = HubEvidenceRegistryService(TransactionEvidenceIdentityRepository(session), clock=self._clock)
            return PiRunEvidenceCompletionService(registry=registry, contexts=self._contexts).complete(
                session=session, task_id=task_id, assignment=projection)

    def reserved_assignment(self, *, task_id, expected_command_id=None, expected_input_digest=None,
                            expected_worker_id=None, expected_worker_url=None, expected_tenant_id=None,
                            expected_workflow_id=None, expected_run_id=None, expected_step_id=None):
        assignment, _ = self._reserved_inputs(task_id=task_id, expected_command_id=expected_command_id,
            expected_input_digest=expected_input_digest, expected_worker_id=expected_worker_id,
            expected_worker_url=expected_worker_url, expected_tenant_id=expected_tenant_id,
            expected_workflow_id=expected_workflow_id, expected_run_id=expected_run_id,
            expected_step_id=expected_step_id)
        return assignment

    def _reserved_inputs(self, *, task_id, expected_command_id=None, expected_input_digest=None,
                            expected_worker_id=None, expected_worker_url=None, expected_tenant_id=None,
                            expected_workflow_id=None, expected_run_id=None, expected_step_id=None):
        """Return only an existing, current Registry reservation for result reads."""
        with sqlite_transaction_guard(self._engine), Session(self._engine) as session, session.begin():
            task = require_locked_row(session, TaskDB, task_id, missing_reason='pi_evidence_task_missing')
            command = pi_task_command(task)
            projection = TransactionPiDispatchEvidenceRepository(session).require(task=task)
            authority = self._authority.require_current(session=session, task=task, command=command, now=self._clock())
            expected = (expected_command_id, expected_input_digest, expected_worker_id, expected_worker_url)
            actual = (command.command_id, native_context_digest(task.worker_execution_context),
                      authority.worker_id, authority.worker_url)
            if any(value is not None and value != bound for value, bound in zip(expected, actual, strict=True)):
                raise ValueError('pi_evidence_worker_read_binding_denied')
            scope = (expected_tenant_id, expected_workflow_id, expected_run_id, expected_step_id)
            if any(value is not None for value in scope) and scope != (
                    command.tenant_id, command.workflow_id, command.run_id, command.node.node_id):
                raise ValueError('pi_evidence_worker_read_scope_denied')
            dispatch_id = 'native-dispatch:' + native_dispatch_binding_digest(command=command, authority=authority)
            registry = HubEvidenceRegistryService(TransactionEvidenceIdentityRepository(session), clock=self._clock)
            registered = registry.assignment_projection(tenant_id=task.tenant_id, project_id=task.project_id,
                task_id=task.id, run_id=projection['run_id'], assignment_id=authority.assignment_id,
                dispatch_lease_id=dispatch_id, input_digest=native_context_digest(task.worker_execution_context),
                expected_binding_digest=projection['binding_digest'])
            if registered != projection:
                raise ValueError('pi_dispatch_reserved_projection_mismatch')
            context = PiContextEvidenceAdmission(registry=registry, contexts=self._contexts).require_reserved(
                session=session, task=task, command=command, worker_id=authority.worker_id, assignment=registered)
            return registered, context

    def reserved_context(self, **binding):
        _, projection = self._reserved_inputs(**binding)
        if projection is None:
            raise ValueError('native_context_bundle_required')
        return projection
