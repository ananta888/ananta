"""Real signed checkpoint identities survive compatibility status projection."""

from dataclasses import replace

import pytest

from agent.services.native_graph_control_bridge import NativeGraphWorkflowControlBridge
from agent.services.workflow_control_bindings import InMemoryWorkflowControlBindingStore, WorkflowControlRunBinding
from agent.services.workflow_control_command_receipts import (
    WorkflowControlCommandReceipt,
    WorkflowControlCommandReceiptError,
    validate_persisted_public_status,
)
from agent.services.workflow_transition_public_projection import canonical_workflow_public_status
from tests.bpmn.completion_helpers import harness as harness
from tests.bpmn.test_idle_checkpoint import waiting_timer
from tests.bpmn.test_workflow_preflight import _timer_workflow
from tests.test_native_graph_runtime import signed_control


def observation(harness):
    request, waiting = waiting_timer(harness)
    workflow = _timer_workflow()
    workflow = replace(workflow, correlation_id=request.correlation_id or request.run_id,
                       metadata={**workflow.metadata, 'project_id': 'synthetic-project',
                                          'policy_version': request.plan.policy_version})
    binding = WorkflowControlRunBinding(tenant_id=request.plan.tenant_id, subject_id='synthetic-owner',
        workflow_id=request.plan.workflow_id, run_id=request.run_id, runtime_id='local',
        plan_hash=request.plan.plan_hash, policy_version=request.plan.policy_version,
        checkpoint_id='synthetic-initial-binding', request=workflow, execution_plan=request.plan.to_dict())
    bridge = NativeGraphWorkflowControlBridge(orchestrator=harness.hub,
                                             bindings=InMemoryWorkflowControlBindingStore())
    return binding, request, waiting, bridge._status(waiting)


def test_public_projection_retains_the_actual_command_checkpoint_and_revision(harness):
    binding, request, waiting, raw = observation(harness)
    public = canonical_workflow_public_status(binding, raw, previous=None)
    assert public['command_binding'] == raw['command_binding']
    assert public['command_binding']['checkpoint_ref'] == waiting.checkpoint.checkpoint_id
    assert public['command_binding']['expected_revision'] == waiting.checkpoint.revision
    assert public['checkpoint_ref'].startswith('local:')  # Existing public compatibility field remains stable.
    assert canonical_workflow_public_status(binding, public, previous=public) == public
    command = signed_control(keys=harness.keys, checkpoint=waiting.checkpoint, command_type='pause',
                             step_id='__workflow__', command_id='synthetic-projected-pause')
    assert command.checkpoint_id == public['command_binding']['checkpoint_ref']
    assert command.expected_revision == public['command_binding']['expected_revision']
    assert harness.hub.resume(request, command=command).status == 'paused'


@pytest.mark.parametrize('change', [
    {'expected_revision': True}, {'expected_revision': 999}, {'run_id': 'foreign'},
    {'plan_hash': 'foreign'}, {'checkpoint_ref': 'wfc-' + 'f' * 32}, {'schema': 'unknown'},
])
def test_mixed_or_malformed_command_bindings_fail_closed(harness, change):
    binding, _, _, raw = observation(harness)
    raw['command_binding'] = {**raw['command_binding'], **change}
    with pytest.raises(ValueError, match='workflow_runtime_command_binding_mismatch'):
        canonical_workflow_public_status(binding, raw, previous=None)


def test_receipt_completion_uses_execution_progress_and_rejects_mixed_scope(harness):
    binding, request, waiting, raw = observation(harness)
    before = canonical_workflow_public_status(binding, raw, previous=None)
    command = signed_control(keys=harness.keys, checkpoint=waiting.checkpoint, command_type='pause',
                             step_id='start', command_id='synthetic-receipt-pause')
    receipt = WorkflowControlCommandReceipt(command_id=command.command_id, tenant_id=command.tenant_id,
        workflow_id=command.workflow_id, run_id=command.run_id, actor_id=command.actor_id,
        command_type=command.command_type, expected_revision=command.expected_revision,
        checkpoint_ref=command.checkpoint_id, request_payload={'admitted_command': command.to_dict()})
    paused = harness.hub.resume(request, command=command)
    bridge = NativeGraphWorkflowControlBridge(orchestrator=harness.hub,
                                             bindings=InMemoryWorkflowControlBindingStore())
    public = canonical_workflow_public_status(binding, bridge._status(paused), previous=before)
    assert public['command_binding']['expected_revision'] > receipt.expected_revision
    validate_persisted_public_status(receipt, binding, public)
    with pytest.raises(WorkflowControlCommandReceiptError, match='observation_pending'):
        validate_persisted_public_status(receipt, binding, before)
    with pytest.raises(WorkflowControlCommandReceiptError, match='public_status_invalid'):
        validate_persisted_public_status(receipt, binding, {**public, 'tenant_id': 'foreign'})


@pytest.mark.parametrize('resolution', ['released', 'changed', 'busy'])
def test_sql_admission_retries_only_unexecuted_contention_and_never_rebases(harness, resolution):
    from sqlmodel import Session, select

    from agent.db_models.workflow_runtime import WorkflowCommandNonceDB, WorkflowControlBindingDB
    from agent.services.workflow_control_command_receipt_persistence import SQLAlchemyWorkflowControlCommandReceiptStore
    from agent.services.workflow_control_persistence import SQLAlchemyWorkflowControlBindingStore

    binding, _, waiting, raw = observation(harness)
    bindings = SQLAlchemyWorkflowControlBindingStore(harness.grant_database, clock=lambda: harness.now)
    bindings.put(binding)
    bindings.record_status(binding.workflow_id, raw)
    with Session(harness.grant_database) as session:
        row = session.get(WorkflowControlBindingDB, binding.workflow_id)
        row.scheduler_owner = 'synthetic-reconciler'
        row.scheduler_lease_expires_at = harness.now + 30
        session.add(row)
        session.commit()
    waits = []

    def release(seconds):
        waits.append(seconds)
        assert receipts.get('synthetic-contended-pause') is None
        with Session(harness.grant_database) as session:
            assert session.exec(select(WorkflowCommandNonceDB)).all() == []
            if resolution == 'busy':
                return
            row = session.get(WorkflowControlBindingDB, binding.workflow_id)
            row.scheduler_owner = ''
            row.scheduler_lease_expires_at = 0
            if resolution == 'changed':
                row.runtime_revision += 1
                row.runtime_checkpoint_ref = 'wfc-' + 'f' * 32
            session.add(row)
            session.commit()

    receipts = SQLAlchemyWorkflowControlCommandReceiptStore(harness.grant_database,
        clock=lambda: harness.now, retry_wait=release)
    command = signed_control(keys=harness.keys, checkpoint=waiting.checkpoint, command_type='pause',
                             step_id='start', command_id='synthetic-contended-pause')
    values = dict(binding=binding, command_id=command.command_id, actor_id=command.actor_id,
        command_type=command.command_type, expected_revision=command.expected_revision,
        checkpoint_ref=command.checkpoint_id, request_payload={'admitted_command': command.to_dict()})
    if resolution == 'released':
        receipt = receipts.stage(**values)
        assert receipt.expected_revision == command.expected_revision
        assert waits == [0.05]
        assert receipts.stage(**values) == receipt
    else:
        with pytest.raises(WorkflowControlCommandReceiptError, match='stage_conflict'):
            receipts.stage(**values)
        assert receipts.get(command.command_id) is None
        assert waits == ([0.05] if resolution == 'changed' else [0.05, 0.1, 0.2, 0.4])
    with Session(harness.grant_database) as session:
        assert len(session.exec(select(WorkflowCommandNonceDB)).all()) == (1 if resolution == 'released' else 0)
