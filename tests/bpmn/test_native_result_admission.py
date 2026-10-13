"""Synthetic Native result admission through actual SQL Task save/CAS recipients."""

import copy
from dataclasses import replace

import pytest
from sqlmodel import Session, select

from agent.common.native_task_result_binding import NATIVE_RESULT_RECEIPT
from agent.db_models.workflow_runtime import WorkflowExecutionOwnershipDB, WorkflowWorkerAssignmentDB
from agent.repositories.tasks import TaskRepository
from agent.services.native_graph_task_queue_adapter import AnantaHubTaskQueueAdapter
from agent.services.native_result_authority import SQLAlchemyNativeResultAuthority
from agent.services.native_result_completion_policy import NativeResultCompletionPolicy
from agent.services.native_result_forwarding import validate_forwarded_native_result
from tests.pi.test_pi_result_completion_policy import admission, complete  # noqa: F401 -- shared SQL fixture


@pytest.fixture
def native(admission):  # noqa: F811 -- imported pytest fixture
    # Reuse the real SQL authority fixture, but exercise a non-Pi command and
    # the generic Native envelope. No model/hardware/production claim is made.
    command = replace(admission.command, node=replace(admission.command.node, task_kind="tool_task"))
    task = admission.repository.get_by_id(admission.task_id)
    task.task_kind = command.node.task_kind
    task.worker_execution_context["native_node_command"] = command.to_dict()
    with Session(admission.engine) as session:
        session.merge(task)
        session.commit()
    admission.command = command
    admission.policy = NativeResultCompletionPolicy(
        authority=SQLAlchemyNativeResultAuthority(key_ring=lambda: admission.keys),
        clock=lambda: admission.clock.now,
    )
    admission.repository = TaskRepository(completion_policy=admission.policy)
    return admission


@pytest.mark.parametrize("mode", ["save", "cas"])
def test_native_result_admission_commits_receipt_with_task(native, mode):
    task = complete(native, mode)
    receipt = task.verification_status[NATIVE_RESULT_RECEIPT]
    assert receipt["classification"] == "technical_observation"
    assert receipt["authority"]["assignment_revision"] == 1
    assert native.repository.get_by_id(task.id).verification_status == task.verification_status
    queue = AnantaHubTaskQueueAdapter(task_queue=None, task_repository=native.repository, task_runtime=None)
    assert queue.poll(
        tenant_id=task.tenant_id, run_id=native.command.run_id, hub_task_ids=(task.id,),
    )[0].status == "completed"


@pytest.mark.parametrize("field,value", [
    ("attempt_id", "foreign-attempt"), ("fencing_token", 99), ("fencing_token", True),
    ("hub_task_id", "foreign-task"), ("node_id", "foreign-node"), ("tenant_id", "foreign-tenant"),
    ("budget_usage", {"tokens": float("nan")}), ("output_data", {"secret": "must-not-persist"}),
])
@pytest.mark.parametrize("mode", ["save", "cas"])
def test_hostile_worker_result_never_mutates_task(native, field, value, mode):
    nested = native.response["workflow_adapter_verification"]["workflow_adapter_task_result"]["adapter_result"]
    nested[field] = value
    nested["verification"]["native_node_result"][field] = value
    with pytest.raises(ValueError):
        complete(native, mode)
    stored = native.repository.get_by_id(native.task_id)
    assert stored.status == "in_progress" and not stored.verification_status and stored.last_output is None


@pytest.mark.parametrize("model,field,value", [
    (WorkflowExecutionOwnershipDB, "attempt_id", "new-attempt"),
    (WorkflowExecutionOwnershipDB, "fencing_token", 2),
    (WorkflowExecutionOwnershipDB, "lease_expires_at", 1),
    (WorkflowWorkerAssignmentDB, "worker_id", "foreign-worker"),
    (WorkflowWorkerAssignmentDB, "hub_task_id", "foreign-task"),
])
def test_stale_authority_rolls_back_task_result(native, model, field, value):
    with Session(native.engine) as session:
        row = session.exec(select(model)).one()
        setattr(row, field, value)
        session.add(row)
        session.commit()
    with pytest.raises((ValueError, RuntimeError)):
        complete(native)
    assert native.repository.get_by_id(native.task_id).status == "in_progress"


def test_exact_replay_after_expiry_preserves_original_receipt(native):
    first = complete(native)
    native.clock.now += 1000
    assert complete(native).verification_status == first.verification_status


@pytest.mark.parametrize("change", ["output", "receipt", "reopen", "command", "scope"])
def test_accepted_result_cannot_be_rebound_or_replaced(native, change):
    task = complete(native)
    if change == "output":
        task.last_output = "replacement"
    elif change == "receipt":
        task.verification_status[NATIVE_RESULT_RECEIPT]["classification"] = "production"
    elif change == "reopen":
        task.status = "in_progress"
    elif change == "command":
        task.worker_execution_context["native_node_command"]["attempt_id"] = "replacement"
    else:
        task.tenant_id = "foreign"
    with pytest.raises(ValueError):
        native.repository.save(task)
    assert native.repository.get_by_id(task.id).status == "completed"


def test_uncomposed_repository_cannot_accept_native_result(native):
    native.repository = TaskRepository()
    with pytest.raises(RuntimeError, match="native_result_completion_policy_unavailable"):
        complete(native)


def test_forwarding_rejects_conflicting_witness_before_projection(native):
    task = native.repository.get_by_id(native.task_id)
    validate_forwarded_native_result(
        task_id=task.id, dispatched_task=task.model_dump(), response=native.response,
        load_task=native.repository.get_by_id,
    )
    response = copy.deepcopy(native.response)
    nested = response["workflow_adapter_verification"]["workflow_adapter_task_result"]["adapter_result"]
    nested["verification"]["native_node_result"]["attempt_id"] = "unassigned"
    with pytest.raises(ValueError):
        validate_forwarded_native_result(
            task_id=task.id, dispatched_task=task.model_dump(), response=response,
            load_task=native.repository.get_by_id,
        )
    assert native.repository.get_by_id(task.id).status == "in_progress"


def test_production_composition_applies_generic_native_admission(native, monkeypatch):
    from agent.repository import _HubTaskCompletionPolicy

    monkeypatch.setattr(
        "agent.services.workflow_hub_task_gateway_runtime.get_workflow_authorization_key_ring", lambda: native.keys,
    )
    native.repository = TaskRepository(completion_policy=_HubTaskCompletionPolicy())
    assert complete(native).verification_status[NATIVE_RESULT_RECEIPT]["authority"]["worker_id"] == "worker-1"
