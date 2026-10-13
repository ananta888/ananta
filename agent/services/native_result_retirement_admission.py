"""Hub SQL proof that an exact accepted result can become a replay-only marker.

No Worker imports, timers, identity issuance or independent task orchestration.
The caller retains all authority locks through its surrounding transaction.
"""

import math

from agent.db_models.agents import AgentInfoDB
from agent.db_models.native_dispatch import NativeDispatchDB
from agent.db_models.pi_dispatch_evidence import PiDispatchEvidenceDB
from agent.db_models.tasks import TaskDB
from agent.db_models.workflow_runtime import (
    WorkflowAuthorizationGrantDB,
    WorkflowExecutionAttemptHistoryDB,
    WorkflowExecutionOwnershipDB,
)
from agent.repositories.sqlalchemy_support import require_locked_row, stable_row_id
from agent.services.native_dispatch_binding import native_dispatch_binding_digest
from agent.services.native_dispatch_journal import require_native_dispatch_receipt
from agent.services.native_result_maintenance_owner import require_registered_result_store_owner
from agent.services.native_result_task_projection import native_task_command
from agent.services.workflow_authorization_grant_service import require_expired_workflow_grant_row
from agent.services.workflow_runtime.sqlalchemy_ownership import (
    execution_ownership_from_row,
    execution_ownership_history_from_row,
)
from ananta_contracts.native_context_bundle import native_context_digest
from ananta_contracts.native_result_binding import native_result_binding
from ananta_contracts.native_result_maintenance import maintenance_input
from ananta_contracts.native_result_retirement import RETIREMENT_CAPABILITY


def _locked(session, model, identity):
    return require_locked_row(session, model, identity, missing_reason='native_result_retirement_proof_missing')


def require_retirable_result(*, session, task_id, tenant_id, project_id, owner, now):
    """Return a content-free slot binding only from admitted, inactive Hub facts.

    This is historical admission, not permission to execute maintenance. The
    issuer must separately validate the current maintenance Task and grant.
    Missing or uncertain facts are rejected. Supersession needs exact Hub history.
    """
    if type(now) not in (int, float) or not math.isfinite(now) or not session.in_transaction():
        raise ValueError('native_result_retirement_transaction_required')
    _, owner = maintenance_input({'batch_size': 1, 'result_store_owner': owner})
    task = _locked(session, TaskDB, task_id)
    if (task.tenant_id != tenant_id or task.project_id != project_id or not project_id
            or task.status not in {'completed', 'failed', 'cancelled'}):
        raise ValueError('native_result_retirement_terminal_scope_denied')
    command = native_task_command(task)
    journal = _locked(session, NativeDispatchDB, stable_row_id('native-dispatch', tenant_id, task_id))
    receipt = require_native_dispatch_receipt(task)
    authority = receipt.get('authority') or {}
    if (journal.state != 'accepted' or journal.worker_id != owner['worker_id']
            or journal.worker_url != owner['worker_url'] or task.assigned_agent_url != journal.worker_url
            or journal.command_digest != native_context_digest(command.to_dict())
            or journal.binding_digest != native_dispatch_binding_digest(task,
                worker_id=journal.worker_id, worker_url=journal.worker_url,
                assignment_revision=journal.assignment_revision)
            or authority.get('assignment_revision') != journal.assignment_revision
            or authority.get('worker_id') != journal.worker_id or authority.get('worker_url') != journal.worker_url):
        raise ValueError('native_result_retirement_dispatch_denied')
    worker = _locked(session, AgentInfoDB, journal.worker_url)
    require_registered_result_store_owner(worker, owner, now=now)
    if RETIREMENT_CAPABILITY not in (worker.authorized_capabilities or ()):
        raise ValueError('native_result_retirement_worker_not_authorized')
    envelope = command.authorization
    grant = _locked(session, WorkflowAuthorizationGrantDB, envelope.envelope_id)
    require_expired_workflow_grant_row(grant, envelope, now=now)
    ownership = execution_ownership_from_row(_locked(session, WorkflowExecutionOwnershipDB,
        stable_row_id('wfro', tenant_id, command.run_id, command.node.node_id)))
    expected = {'tenant_id': tenant_id, 'workflow_id': command.workflow_id, 'run_id': command.run_id,
                'step_id': command.node.node_id, 'attempt_id': command.attempt_id,
                'fencing_token': command.fencing_token}
    superseded = (ownership.tenant_id == tenant_id and ownership.workflow_id == command.workflow_id
            and ownership.run_id == command.run_id and ownership.step_id == command.node.node_id
            and ownership.fencing_token > command.fencing_token)
    if superseded:
        revision = authority.get('ownership_revision')
        if type(revision) is not int or revision < 1:
            raise ValueError('native_result_retirement_history_identity_denied')
        ownership = execution_ownership_history_from_row(_locked(session, WorkflowExecutionAttemptHistoryDB,
            stable_row_id('wfrh', tenant_id, command.run_id, command.node.node_id, revision)))
    if (any(getattr(ownership, field) != value for field, value in expected.items())
            or ownership.owner_id != f'hub-native:{command.run_id}:{command.node.node_id}'
            or not math.isfinite(ownership.lease_expires_at)
            or not superseded and ownership.lease_expires_at > now):
        raise ValueError('native_result_retirement_ownership_still_active')
    evidence = None
    if command.node.task_kind == 'pi_coding_agent':
        evidence_row = session.get(PiDispatchEvidenceDB, (tenant_id, task_id))
        if evidence_row is None or evidence_row.project_id != project_id or evidence_row.claim != journal.claim:
            raise ValueError('native_result_retirement_evidence_binding_denied')
        evidence = evidence_row.projection
    _, binding = native_result_binding(task.model_dump(mode='json'), evidence)
    return {'task_id': task.id, 'binding_digest': binding}
