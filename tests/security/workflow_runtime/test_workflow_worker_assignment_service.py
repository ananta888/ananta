from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError

from agent.db_models.workflow_runtime import WorkflowWorkerAssignmentDB
from agent.services.workflow_runtime import InMemoryExecutionOwnershipStore
from agent.services.workflow_worker_assignment_service import (
    InMemoryWorkflowWorkerAssignmentStore,
    SQLAlchemyWorkflowWorkerAssignmentStore,
    WorkflowWorkerAssignment,
    WorkflowWorkerAssignmentError,
    WorkflowWorkerAssignmentService,
)


def _worker(*, worker_id: str, url: str, capability: str):
    return SimpleNamespace(
        name=worker_id,
        url=url,
        role="worker",
        registration_validated=True,
        registration_provenance="strict_registration_keyring_v1",
        authorized_capabilities=[capability],
    )


def _service(owner_id: str):
    ownership = InMemoryExecutionOwnershipStore()
    claim = ownership.claim(
        tenant_id="tenant-1",
        workflow_id="workflow-1",
        run_id="run-1",
        step_id="step-1",
        owner_id=owner_id,
        lease_seconds=300,
        maximum_retries=2,
        now=1_000.0,
    )
    assignments = InMemoryWorkflowWorkerAssignmentStore()
    service = WorkflowWorkerAssignmentService(
        ownership=ownership,
        assignments=assignments,
        clock=lambda: 1_001.0,
    )
    return service, assignments, claim.ownership


def test_native_hub_dispatch_binds_placeholder_lease_to_registered_worker() -> None:
    service, assignments, ownership = _service("hub-native:run-1:step-1")
    task = SimpleNamespace(
        id="native-task-1",
        worker_execution_context={
            "schema": "ananta.native_graph_worker_context.v1",
            "runtime_path": "native_graph_node",
            "native_node_command": {
                "tenant_id": "tenant-1",
                "workflow_id": "workflow-1",
                "run_id": "run-1",
                "node": {"node_id": "step-1"},
                "attempt_id": ownership.attempt_id,
                "fencing_token": ownership.fencing_token,
            },
        },
    )
    worker = _worker(
        worker_id="ananta-worker-1",
        url="http://ai-agent-alpha:5000",
        capability="workflow.adapter.native",
    )

    first = service.bind_dispatched_task(task=task, worker=worker)
    duplicate = service.bind_dispatched_task(task=task, worker=worker)

    assert first == duplicate
    assert first is not None
    assert first.worker_id == "ananta-worker-1"
    assert first.attempt_id == ownership.attempt_id
    assert assignments.get(
        tenant_id="tenant-1",
        run_id="run-1",
        step_id="step-1",
    ) == first


def test_langgraph_dispatch_binds_queue_lease_and_rejects_worker_handoff() -> None:
    service, _assignments, ownership = _service(
        "workflow-adapter-task-queue"
    )
    task = SimpleNamespace(
        id="adapter-task-1",
        worker_execution_context={
            "schema": "ananta.workflow-adapter-worker-task.v1",
            "tenant_id": "tenant-1",
            "workflow_id": "workflow-1",
            "run_id": "run-1",
            "step_id": "step-1",
            "attempt_id": ownership.attempt_id,
            "fencing_token": ownership.fencing_token,
            "owner_id": "workflow-adapter-task-queue",
        },
    )
    first_worker = _worker(
        worker_id="ananta-langgraph-worker-1",
        url="http://ai-agent-langgraph-worker:5000",
        capability="workflow.adapter.langgraph",
    )
    foreign_worker = _worker(
        worker_id="ananta-langgraph-worker-2",
        url="http://ai-agent-langgraph-worker-2:5000",
        capability="workflow.adapter.langgraph",
    )

    assignment = service.bind_dispatched_task(task=task, worker=first_worker)
    assert assignment is not None
    assert assignment.worker_url == "http://ai-agent-langgraph-worker:5000"

    with pytest.raises(WorkflowWorkerAssignmentError) as raised:
        service.bind_dispatched_task(task=task, worker=foreign_worker)
    assert raised.value.reason_code == (
        "workflow_worker_assignment_identity_conflict"
    )


def test_assignment_rejects_unproven_worker_and_tampered_hub_owner() -> None:
    service, _assignments, ownership = _service("attacker-controlled-owner")
    task = SimpleNamespace(
        id="native-task-1",
        worker_execution_context={
            "schema": "ananta.native_graph_worker_context.v1",
            "native_node_command": {
                "tenant_id": "tenant-1",
                "workflow_id": "workflow-1",
                "run_id": "run-1",
                "node": {"node_id": "step-1"},
                "attempt_id": ownership.attempt_id,
                "fencing_token": ownership.fencing_token,
            },
        },
    )
    unproven = _worker(
        worker_id="ananta-worker-1",
        url="http://ai-agent-alpha:5000",
        capability="workflow.adapter.native",
    )
    unproven.registration_provenance = "legacy"

    with pytest.raises(WorkflowWorkerAssignmentError) as identity_denied:
        service.bind_dispatched_task(task=task, worker=unproven)
    assert identity_denied.value.reason_code == (
        "workflow_worker_assignment_registry_identity_denied"
    )

    trusted = _worker(
        worker_id="ananta-worker-1",
        url="http://ai-agent-alpha:5000",
        capability="workflow.adapter.native",
    )
    with pytest.raises(WorkflowWorkerAssignmentError) as lease_denied:
        service.bind_dispatched_task(task=task, worker=trusted)
    assert lease_denied.value.reason_code == (
        "workflow_worker_assignment_lease_mismatch"
    )


def test_sql_assignment_binding_is_persistent_and_idempotent() -> None:
    engine = create_engine("sqlite://")
    WorkflowWorkerAssignmentDB.__table__.create(engine)
    store = SQLAlchemyWorkflowWorkerAssignmentStore(engine)
    assignment = WorkflowWorkerAssignment(
        tenant_id="tenant-1",
        workflow_id="workflow-1",
        run_id="run-1",
        step_id="step-1",
        attempt_id="attempt-1",
        fencing_token=1,
        hub_task_id="task-1",
        worker_id="worker-1",
        worker_url="http://worker-1:5000",
        assigned_at=1_001.0,
    )

    assert store.bind(assignment) == assignment
    assert store.bind(assignment) == assignment
    assert store.get(
        tenant_id="tenant-1",
        run_id="run-1",
        step_id="step-1",
    ) == assignment


def test_sql_assignment_binding_retries_a_concurrent_first_insert(
    monkeypatch,
) -> None:
    store = SQLAlchemyWorkflowWorkerAssignmentStore(create_engine("sqlite://"))
    assignment = WorkflowWorkerAssignment(
        tenant_id="tenant-1",
        workflow_id="workflow-1",
        run_id="run-1",
        step_id="step-1",
        attempt_id="attempt-1",
        fencing_token=1,
        hub_task_id="task-1",
        worker_id="worker-1",
        worker_url="http://worker-1:5000",
        assigned_at=1_001.0,
    )
    calls = 0

    def bind_once(candidate):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise IntegrityError("INSERT", {}, RuntimeError("unique race"))
        return candidate

    monkeypatch.setattr(store, "_bind_once", bind_once)

    assert store.bind(assignment) == assignment
    assert calls == 2


def test_strict_runtime_reloads_hub_selected_worker_from_registry(
    monkeypatch,
) -> None:
    from agent.services import repository_registry
    from agent.services import workflow_worker_assignment_runtime as runtime

    registered = _worker(
        worker_id="worker-1",
        url="http://worker-1:5000",
        capability="workflow.adapter.native",
    )
    selected = SimpleNamespace(
        name="worker-1",
        url="http://worker-1:5000",
        authorized_capabilities=["attacker-controlled"],
    )
    captured = SimpleNamespace(worker=None)

    class AssignmentService:
        def bind_dispatched_task(self, *, task, worker):
            captured.worker = worker
            return None

    monkeypatch.setattr(
        repository_registry,
        "get_repository_registry",
        lambda: SimpleNamespace(
            agent_repo=SimpleNamespace(get_by_url=lambda _url: registered)
        ),
    )
    monkeypatch.setattr(
        runtime,
        "get_workflow_worker_assignment_service",
        lambda: AssignmentService(),
    )

    runtime.bind_dispatched_workflow_task(
        task=SimpleNamespace(id="task-1"),
        worker=selected,
        config={"ANANTA_WORKFLOW_REQUIRE_REGISTERED_WORKER_AUTH": True},
    )

    assert captured.worker is registered


def test_strict_runtime_rejects_worker_missing_from_hub_registry(
    monkeypatch,
) -> None:
    from agent.services import repository_registry
    from agent.services.workflow_worker_assignment_runtime import (
        bind_dispatched_workflow_task,
    )

    monkeypatch.setattr(
        repository_registry,
        "get_repository_registry",
        lambda: SimpleNamespace(
            agent_repo=SimpleNamespace(get_by_url=lambda _url: None)
        ),
    )

    with pytest.raises(WorkflowWorkerAssignmentError) as raised:
        bind_dispatched_workflow_task(
            task=SimpleNamespace(id="task-1"),
            worker=SimpleNamespace(
                name="synthetic-worker",
                url="http://synthetic-worker:5000",
            ),
            config={"ANANTA_WORKFLOW_REQUIRE_REGISTERED_WORKER_AUTH": True},
        )
    assert raised.value.reason_code == (
        "workflow_worker_assignment_registry_identity_denied"
    )
