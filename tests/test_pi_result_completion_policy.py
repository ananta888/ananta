"""Atomic Pi admission through actual Task save/CAS and SQL authority rows."""

import copy
from dataclasses import asdict
from types import SimpleNamespace

import pytest
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session, SQLModel, create_engine, select

from agent.common.pi_task_result_binding import PI_RESULT_RECEIPT
from agent.db_models import TaskDB
from agent.db_models.agents import AgentInfoDB
from agent.db_models.workflow_runtime import (
    WorkflowAuthorizationGrantDB,
    WorkflowExecutionOwnershipDB,
    WorkflowWorkerAssignmentDB,
)
from agent.repositories.pi_result_authority import SQLAlchemyPiResultAuthority
from agent.repositories.tasks import TaskRepository
from agent.services.native_graph_task_queue_adapter import AnantaHubTaskQueueAdapter
from agent.services.pi_result_completion_policy import PiResultCompletionPolicy
from agent.services.workflow_authorization_grant_service import SQLAlchemyWorkflowAuthorizationGrantService
from agent.services.workflow_runtime import HmacKeyRing
from agent.services.workflow_runtime.ownership import ExecutionOwnership
from agent.services.workflow_runtime.sqlalchemy_support import stable_row_id
from agent.services.workflow_worker_assignment_service import (
    SQLAlchemyWorkflowWorkerAssignmentStore,
    WorkflowWorkerAssignment,
)
from agent.services.workflow_worker_service_auth import STRICT_WORKER_REGISTRATION_PROVENANCE
from tests.test_pi_native_result_envelope import actual_route


@pytest.fixture
def admission(tmp_path, monkeypatch):
    response, command, task_id = actual_route(tmp_path)
    engine = create_engine(f"sqlite:///{tmp_path / 'pi-admission.sqlite3'}")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("agent.repositories.tasks._engine", lambda: engine)
    now = command.authorization.issued_at
    clock = SimpleNamespace(now=now)
    keys = HmacKeyRing({"key-1": b"x" * 32}, active_key_id="key-1")
    worker = AgentInfoDB(
        url="http://worker-1:5000", name="worker-1", registration_validated=True,
        registration_provenance=STRICT_WORKER_REGISTRATION_PROVENANCE,
        authorized_capabilities=["workflow.adapter.native", "coding.agent.pi", "text_generation"],
    )
    task = TaskDB(
        id=task_id, tenant_id=command.tenant_id, status="in_progress", task_kind="pi_coding_agent",
        derivation_reason="native_graph_hub_delegation", assigned_agent_url=worker.url,
        worker_execution_context={
            "schema": "ananta.native_graph_worker_context.v1", "runtime_path": "native_graph_node",
            "native_node_command": command.to_dict(),
        },
    )
    ownership = ExecutionOwnership(
        tenant_id=command.tenant_id, workflow_id=command.workflow_id, run_id=command.run_id,
        step_id=command.node.node_id, attempt_id=command.attempt_id, fencing_token=command.fencing_token,
        owner_id=f"hub-native:{command.run_id}:{command.node.node_id}", revision=1, status="active",
        lease_expires_at=now + 300, last_heartbeat_at=now - 1,
    )
    fields = asdict(ownership)
    for name in ("schema", "result_ack_key", "failure_code"):
        fields.pop(name)
    with Session(engine, expire_on_commit=False) as session:
        session.add(worker)
        session.add(task)
        session.add(WorkflowExecutionOwnershipDB(
            id=stable_row_id("wfro", command.tenant_id, command.run_id, command.node.node_id),
            ownership=ownership.to_dict(), **fields,
        ))
        session.commit()
    SQLAlchemyWorkflowWorkerAssignmentStore(engine).bind(WorkflowWorkerAssignment(
        tenant_id=command.tenant_id, workflow_id=command.workflow_id, run_id=command.run_id,
        step_id=command.node.node_id, attempt_id=command.attempt_id, fencing_token=command.fencing_token,
        hub_task_id=task_id, worker_id=worker.name, worker_url=worker.url, assigned_at=now - 1,
    ))
    grants = SQLAlchemyWorkflowAuthorizationGrantService(engine, clock=lambda: now)
    grants.grant(command.authorization)
    policy = PiResultCompletionPolicy(
        authority=SQLAlchemyPiResultAuthority(key_ring=lambda: keys), clock=lambda: clock.now,
    )
    repository = TaskRepository(completion_policy=policy)
    yield SimpleNamespace(
        repository=repository, policy=policy, engine=engine, command=command, task_id=task_id,
        response=response, clock=clock, grants=grants, keys=keys,
    )
    engine.dispose()


def result_fields(response):
    outer = copy.deepcopy(response["workflow_adapter_verification"]["workflow_adapter_task_result"])
    return {
        "last_output": response["output"], "last_exit_code": response["exit_code"],
        "verification_status": {
            "workflow_adapter_task_result": outer,
            "native_node_result": copy.deepcopy(outer["adapter_result"]["verification"]["native_node_result"]),
        },
    }


def complete(admission, mode="save"):
    fields = result_fields(admission.response)
    if mode == "cas":
        def mutate(task):
            for field, value in fields.items():
                setattr(task, field, value)

        result = admission.repository.compare_and_set_status(
            admission.task_id, expected_statuses={"in_progress"}, target_status="completed", mutate=mutate,
        )
        assert result.updated
        return result.task
    task = admission.repository.get_by_id(admission.task_id)
    task.status = "completed"
    for field, value in fields.items():
        setattr(task, field, value)
    return admission.repository.save(task)


@pytest.mark.parametrize("mode", ["save", "cas"])
def test_pi_completion_commits_exact_hub_receipt_with_task(admission, mode):
    task = complete(admission, mode)
    receipt = task.verification_status[PI_RESULT_RECEIPT]
    assert receipt["classification"] == "technical_observation"
    assert receipt["authority"]["worker_id"] == "worker-1"
    assert receipt["authority"]["assignment_revision"] == 1
    assert len(receipt["command_digest"]) == len(receipt["result_digest"]) == 64
    assert "SRC_" not in str(receipt) and "RUN_" not in str(receipt)
    assert admission.repository.get_by_id(task.id).verification_status == task.verification_status
    hub = AnantaHubTaskQueueAdapter(task_queue=None, task_repository=admission.repository, task_runtime=None)
    accepted = hub.poll(tenant_id=task.tenant_id, run_id=admission.command.run_id, hub_task_ids=(task.id,))
    assert len(accepted) == 1 and accepted[0].output_data["output"] == "A\u2028B"


@pytest.mark.parametrize("model,field,value", [
    (WorkflowExecutionOwnershipDB, "attempt_id", "new-attempt"),
    (WorkflowExecutionOwnershipDB, "fencing_token", 2),
    (WorkflowExecutionOwnershipDB, "status", "failed"),
    (WorkflowWorkerAssignmentDB, "hub_task_id", "foreign-task"),
    (WorkflowWorkerAssignmentDB, "worker_url", "http://foreign:5000"),
    (WorkflowWorkerAssignmentDB, "worker_id", "foreign-worker"),
    (WorkflowWorkerAssignmentDB, "attempt_id", "foreign-attempt"),
    (AgentInfoDB, "registration_validated", False),
    (AgentInfoDB, "registration_provenance", "legacy"),
    (AgentInfoDB, "authorized_capabilities", ["workflow.adapter.native", "text_generation"]),
    (WorkflowAuthorizationGrantDB, "grant_digest", "a" * 64),
])
def test_authority_change_never_commits_worker_completion(admission, model, field, value):
    with Session(admission.engine) as session:
        row = session.exec(select(model)).one()
        setattr(row, field, value)
        session.add(row)
        session.commit()
    with pytest.raises((ValueError, RuntimeError)):
        complete(admission)
    task = admission.repository.get_by_id(admission.task_id)
    assert task.status == "in_progress" and not task.verification_status


def test_expired_lease_and_revoked_grant_fail_closed(admission):
    admission.clock.now += 301
    with pytest.raises(ValueError, match="pi_native_result_lease_not_current"):
        complete(admission)
    admission.clock.now -= 301
    admission.grants.revoke(admission.command.authorization.envelope_id, reason_code="synthetic-revocation")
    with pytest.raises(RuntimeError, match="workflow_authorization_grant_not_current"):
        complete(admission)


def test_exact_replay_preserves_receipt_without_renewing_authority(admission):
    first = complete(admission)
    admission.clock.now += 1000
    repeated = complete(admission)
    assert repeated.verification_status == first.verification_status


@pytest.mark.parametrize("change", ["result", "receipt", "receipt_bool", "reopen", "scope", "command"])
def test_accepted_result_cannot_be_overwritten_or_rebound(admission, change):
    task = complete(admission)
    if change == "result":
        task.last_output = "replacement"
    elif change == "receipt":
        task.verification_status[PI_RESULT_RECEIPT]["classification"] = "production"
    elif change == "receipt_bool":
        task.verification_status[PI_RESULT_RECEIPT]["authority"]["assignment_revision"] = True
    elif change == "reopen":
        task.status = "in_progress"
    elif change == "scope":
        task.project_id = "foreign-project"
    else:
        task.worker_execution_context["native_node_command"]["input_data"] = {"prompt": "replacement"}
    with pytest.raises(ValueError):
        admission.repository.save(task)
    assert admission.repository.get_by_id(task.id).status == "completed"


def test_uncomposed_repository_cannot_complete_pi(admission):
    admission.repository = TaskRepository()
    with pytest.raises(RuntimeError, match="pi_native_result_completion_policy_unavailable"):
        complete(admission)


def test_missing_worker_result_cannot_be_promoted_to_success(admission):
    task = admission.repository.get_by_id(admission.task_id)
    task.status = "completed"
    with pytest.raises(ValueError, match="pi_native_result_required"):
        admission.repository.save(task)
    task.status = "failed"
    assert admission.repository.save(task).status == "failed"


def test_production_composition_keeps_pi_policy_after_organization_policy(admission, monkeypatch):
    from agent.repository import _HubTaskCompletionPolicy

    monkeypatch.setattr(
        "agent.services.workflow_hub_task_gateway_runtime.get_workflow_authorization_key_ring", lambda: admission.keys,
    )
    admission.repository = TaskRepository(completion_policy=_HubTaskCompletionPolicy())
    assert complete(admission).verification_status[PI_RESULT_RECEIPT]["authority"]["worker_id"] == "worker-1"


def test_forwarded_worker_route_reaches_real_task_status_persistence(admission, monkeypatch):
    from agent.services import _task_scoped_forwarding as forwarding
    from agent.services import task_runtime_service

    monkeypatch.setattr(forwarding, "get_repository_registry", lambda: SimpleNamespace(task_repo=admission.repository))
    monkeypatch.setattr(task_runtime_service, "task_repo", admission.repository)
    task = admission.repository.get_by_id(admission.task_id).model_dump()
    forwarding.persist_forwarded_execution(
        tid=admission.task_id, response=admission.response, task=task, request_data=SimpleNamespace(command=None),
    )
    stored = admission.repository.get_by_id(admission.task_id)
    assert stored.status == "completed"
    assert stored.verification_status[PI_RESULT_RECEIPT]["classification"] == "technical_observation"


@pytest.mark.parametrize("change", ["missing_result", "extra_field", "wrong_worker", "hidden_kind"])
def test_original_forwarding_response_cannot_bypass_pi_policy(admission, monkeypatch, change):
    from agent.services import _task_scoped_forwarding as forwarding

    monkeypatch.setattr(forwarding, "get_repository_registry", lambda: SimpleNamespace(task_repo=admission.repository))
    task = admission.repository.get_by_id(admission.task_id).model_dump()
    if change == "missing_result":
        admission.response = {"status": "failed", "output": "unbound"}
    elif change == "extra_field":
        admission.response["unregistered_evidence"] = "must-not-accept"
    elif change == "wrong_worker":
        task["assigned_agent_url"] = "http://foreign-worker:5000"
    else:
        task["task_kind"] = "shell"
        task["worker_execution_context"] = {}
    with pytest.raises(ValueError, match="^pi_native_result_"):
        forwarding.persist_forwarded_execution(
            tid=admission.task_id, response=admission.response, task=task, request_data=SimpleNamespace(command=None),
        )
    assert admission.repository.get_by_id(admission.task_id).status == "in_progress"


def test_legacy_unreceipted_terminal_cannot_be_reopened_for_posthoc_acceptance(admission):
    with Session(admission.engine) as session:
        task = session.get(TaskDB, admission.task_id)
        task.status = "completed"
        session.add(task)
        session.commit()
    task = admission.repository.get_by_id(admission.task_id)
    task.status = "in_progress"
    with pytest.raises(ValueError, match="pi_native_result_terminal_task_immutable"):
        admission.repository.save(task)


@pytest.mark.parametrize("change", ["missing", "scope", "digest", "classification", "revision", "extra"])
def test_poll_rejects_missing_or_corrupted_admission_receipt(admission, change):
    complete(admission)
    with Session(admission.engine) as session:
        task = session.get(TaskDB, admission.task_id)
        verification = copy.deepcopy(task.verification_status)
        receipt = verification[PI_RESULT_RECEIPT]
        if change == "missing":
            verification.pop(PI_RESULT_RECEIPT)
        elif change == "scope":
            receipt["tenant_id"] = "foreign"
        elif change == "digest":
            receipt["result_digest"] = "b" * 64
        elif change == "classification":
            receipt["classification"] = "production"
        elif change == "revision":
            receipt["authority"]["assignment_revision"] = True
        else:
            receipt["unregistered_evidence"] = "not-authority"
        task.verification_status = verification
        # JSON's Python equality considers 1 == True. Force the deliberately
        # corrupted row to disk so this exercises ingress, not ORM dirty checks.
        flag_modified(task, "verification_status")
        session.add(task)
        session.commit()
    hub = AnantaHubTaskQueueAdapter(task_queue=None, task_repository=admission.repository, task_runtime=None)
    with pytest.raises(ValueError, match="^pi_native_result_receipt_"):
        hub.poll(
            tenant_id=admission.command.tenant_id, run_id=admission.command.run_id, hub_task_ids=(admission.task_id,),
        )


def test_current_assignment_does_not_substitute_for_a_valid_signature(admission):
    admission.keys.rotate(key_id="key-1", key=b"z" * 32)
    with pytest.raises(RuntimeError, match="signature"):
        complete(admission)
