"""Approved Pi bytes from caller-session Task/bundle/policy, no nested SQL sessions."""

from agent.db_models import TaskDB
from agent.db_models.blueprints import ContextBundleDB
from agent.repositories.context_policy_transaction import TransactionActiveContextPolicies
from agent.repositories.sqlalchemy_support import require_locked_row
from agent.services.context_access_policy_service import ContextAccessPolicyService
from agent.services.native_context_blocks import NativeContextBlockProjector
from agent.services.native_context_policy_service import NativeContextPolicyService
from agent.services.native_context_snapshot import validate_prepared_native_context
from agent.services.pi_context_evidence import pi_context_reference
from agent.services.pi_result_task_projection import pi_task_command
from ananta_contracts.native_context_bundle import NativeContextBundleProjection, native_context_digest


class TransactionPiApprovedContextReader:
    def __init__(self, *, destinations):
        self._destinations = destinations

    def read(self, *, session, task, command, worker_id):
        if not session.in_transaction():
            raise ValueError('native_context_caller_transaction_required')
        task = require_locked_row(session, TaskDB, task.id, missing_reason='pi_evidence_task_missing')
        stored_command = pi_task_command(task)
        if native_context_digest(command.to_dict()) != native_context_digest(stored_command.to_dict()):
            raise ValueError('pi_context_evidence_command_mismatch')
        reference = pi_context_reference(task)
        if reference is None:
            raise ValueError('native_context_bundle_required')
        bundle = require_locked_row(session, ContextBundleDB, reference, missing_reason='context_bundle_not_found')
        if bundle.task_id != task.id:
            raise ValueError('context_bundle_task_mismatch')
        validate_prepared_native_context(task=task.model_dump(), bundle=bundle, command=command.to_dict())
        policy = NativeContextPolicyService(policies=TransactionActiveContextPolicies(session),
            destinations=self._destinations, blocks=NativeContextBlockProjector(ContextAccessPolicyService()))
        approved = policy.project(task=task.model_dump(), bundle=bundle, command=command, worker_id=worker_id)
        return NativeContextBundleProjection.create(hub_task_id=task.id, bundle_id=bundle.id,
            command=command.to_dict(), approved=approved)
