"""Historical retirement proof from real SQL result admission; synthetic only."""

from types import SimpleNamespace

import pytest
from sqlmodel import Session, select

from agent.db_models import AgentInfoDB, TaskDB
from agent.db_models.native_dispatch import NativeDispatchDB
from agent.db_models.workflow_runtime import WorkflowAuthorizationGrantDB, WorkflowExecutionOwnershipDB
from agent.services.native_dispatch_journal import SQLAlchemyNativeDispatchJournal
from agent.services.native_result_retirement_admission import require_retirable_result
from ananta_contracts.native_result_binding import native_result_binding
from ananta_contracts.native_result_maintenance import MAINTENANCE_CAPABILITY, RESULT_STORE_BINDING_LIMIT
from ananta_contracts.native_result_retirement import RETIREMENT_CAPABILITY
from tests.bpmn.test_native_result_admission import native as native
from tests.pi.test_pi_result_completion_policy import admission as admission
from tests.pi.test_pi_result_completion_policy import complete


@pytest.fixture
def accepted(native):  # noqa: F811 -- imported fixture
    owner = {'worker_id': 'worker-1', 'worker_url': 'http://worker-1:5000', 'store_binding_id': 'private-store'}
    with Session(native.engine) as session, session.begin():
        task = session.get(TaskDB, native.task_id)
        task.project_id = 'synthetic-project'
        session.add(task)
    task = native.repository.get_by_id(native.task_id)
    journal = SQLAlchemyNativeDispatchJournal(native.engine, clock=lambda: native.clock.now)
    claim = journal.begin(task=task, assignment=SimpleNamespace(**{
        'worker_id': owner['worker_id'], 'worker_url': owner['worker_url'], 'revision': 1}))
    completed = complete(native)
    journal.settle(task=completed, claim=claim, accepted=True)
    native.clock.now += 1000
    with Session(native.engine) as session, session.begin():
        worker = session.get(AgentInfoDB, owner['worker_url'])
        worker.last_seen = native.clock.now
        worker.status, worker.role = 'online', 'worker'
        worker.authorized_capabilities = [*worker.authorized_capabilities, MAINTENANCE_CAPABILITY,
                                           RETIREMENT_CAPABILITY]
        worker.execution_limits = {RESULT_STORE_BINDING_LIMIT: owner['store_binding_id']}
        session.add(worker)
    return native, owner, completed


def proof(fixture, **overrides):
    native, owner, task = fixture
    values = {'task_id': task.id, 'tenant_id': task.tenant_id, 'project_id': task.project_id,
              'owner': owner, 'now': native.clock.now, **overrides}
    with Session(native.engine) as session, session.begin():
        return require_retirable_result(session=session, **values)


def test_exact_accepted_dispatch_with_inactive_authority_yields_content_free_binding(accepted):
    _, _, task = accepted
    assert proof(accepted) == {'task_id': task.id, 'binding_digest': native_result_binding(task.model_dump())[1]}
    assert set(proof(accepted)) == {'task_id', 'binding_digest'}


@pytest.mark.parametrize('model,field,value', [
    (NativeDispatchDB, 'state', 'unknown'), (NativeDispatchDB, 'state', 'sending'),
    (NativeDispatchDB, 'binding_digest', '0' * 64), (NativeDispatchDB, 'command_digest', '0' * 64),
    (NativeDispatchDB, 'assignment_revision', 2),
    (WorkflowAuthorizationGrantDB, 'grant_digest', '0' * 64),
    (WorkflowAuthorizationGrantDB, 'expires_at', 'future'),
    (WorkflowExecutionOwnershipDB, 'lease_expires_at', 'future'),
    (WorkflowExecutionOwnershipDB, 'fencing_token', 999),
    (AgentInfoDB, 'execution_limits', {RESULT_STORE_BINDING_LIMIT: 'changed-store'}),
    (AgentInfoDB, 'authorized_capabilities', ['workflow.adapter.native', MAINTENANCE_CAPABILITY]),
    (AgentInfoDB, 'registration_validated', False),
    (TaskDB, 'status', 'in_progress'), (TaskDB, 'verification_status', {}),
])
def test_uncertain_mutated_or_active_hub_facts_deny_retirement(accepted, model, field, value):
    native, *_ = accepted
    with Session(native.engine) as session, session.begin():
        row = session.exec(select(model)).one()
        setattr(row, field, native.clock.now + 1000 if value == 'future' else value)
        session.add(row)
    with pytest.raises((ValueError, RuntimeError)):
        proof(accepted)


@pytest.mark.parametrize('field,value', [('tenant_id', 'foreign'), ('project_id', 'foreign'),
                                        ('task_id', 'unknown'), ('now', float('nan'))])
def test_foreign_scope_or_unknown_identity_is_not_retirement_proof(accepted, field, value):
    with pytest.raises(ValueError):
        proof(accepted, **{field: value})
