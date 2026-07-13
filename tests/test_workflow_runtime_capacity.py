from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import SQLModel, create_engine

from agent.services.workflow_adapter_task_queue_service import (
    WorkflowAdapterQueueError,
    WorkflowAdapterTaskReceipt,
    WorkflowAdapterTaskSubmission,
)
from agent.services.workflow_runtime_capacity_service import (
    CapacityGuardedWorkflowAdapterQueue,
    SQLAlchemyWorkflowRuntimeCapacity,
)


class _Queue:
    def __init__(self) -> None:
        self.statuses: dict[str, str] = {}

    def submit(self, value: WorkflowAdapterTaskSubmission) -> WorkflowAdapterTaskReceipt:
        task_id = f"task-{value.step_id}"
        self.statuses.setdefault(task_id, "created")
        return WorkflowAdapterTaskReceipt(
            hub_task_id=task_id,
            workflow_id=value.workflow_id,
            run_id=value.run_id,
            step_id=value.step_id,
            operation_id=f"operation-{value.step_id}",
            adapter_kind="langgraph",
            command=value.command,
            accepted=True,
            status=self.statuses[task_id],
        )

    def status(self, **scope: str) -> dict[str, str]:
        task_id = scope["hub_task_id"]
        return {"hub_task_id": task_id, "status": self.statuses[task_id]}

    def inspect(self, **scope: str) -> dict[str, str]:
        return self.status(**scope)

    def cancel(self, **scope: str) -> dict[str, str]:
        self.statuses[scope["hub_task_id"]] = "cancelled"
        return self.status(**scope)

    def history(self, **scope: str) -> tuple[dict[str, str], ...]:
        del scope
        return ()


def _submission(
    *,
    workflow_id: str,
    step_id: str,
    run_id: str | None = None,
) -> WorkflowAdapterTaskSubmission:
    return WorkflowAdapterTaskSubmission(
        tenant_id="tenant-a",
        subject_id="owner-a",
        workflow_id=workflow_id,
        run_id=run_id or f"run-{workflow_id}",
        step_id=step_id,
        plan_hash="a" * 64,
        policy_version="policy-v1",
        adapter_kind="langgraph",
        command="dry_run",
        task_type="agent_workflow",
        payload={"parallel_limits": {"tenant": 1, "worker": 2}},
        idempotency_key=f"capacity:{workflow_id}:{step_id}",
        provider_decision_reason="provider_transport_not_required",
    )


def test_capacity_is_global_across_service_instances_and_released_after_restart(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'capacity.db'}")
    SQLModel.metadata.create_all(engine)
    queue = _Queue()
    first_capacity = SQLAlchemyWorkflowRuntimeCapacity(
        engine,
        tenant_limit=4,
        worker_limit=4,
    )
    first = CapacityGuardedWorkflowAdapterQueue(queue, first_capacity)
    first.submit(_submission(workflow_id="workflow-a", step_id="a"))

    restarted_capacity = SQLAlchemyWorkflowRuntimeCapacity(
        engine,
        tenant_limit=4,
        worker_limit=4,
    )
    restarted = CapacityGuardedWorkflowAdapterQueue(queue, restarted_capacity)
    with pytest.raises(
        WorkflowAdapterQueueError,
        match="langgraph_global_tenant_capacity_exhausted",
    ):
        restarted.submit(_submission(workflow_id="workflow-b", step_id="b"))

    queue.statuses["task-a"] = "completed"
    restarted.status(
        tenant_id="tenant-a",
        subject_id="owner-a",
        hub_task_id="task-a",
    )
    receipt = restarted.submit(_submission(workflow_id="workflow-b", step_id="b"))
    assert receipt.hub_task_id == "task-b"


def test_inspect_does_not_release_capacity() -> None:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    queue = _Queue()
    capacity = SQLAlchemyWorkflowRuntimeCapacity(
        engine,
        tenant_limit=1,
        worker_limit=1,
    )
    guarded = CapacityGuardedWorkflowAdapterQueue(queue, capacity)
    guarded.submit(_submission(workflow_id="workflow-a", step_id="a"))
    queue.statuses["task-a"] = "completed"
    guarded.inspect(
        tenant_id="tenant-a",
        subject_id="owner-a",
        hub_task_id="task-a",
    )
    with pytest.raises(WorkflowAdapterQueueError):
        guarded.submit(_submission(workflow_id="workflow-b", step_id="b"))


def test_available_slots_excludes_only_the_current_run_not_the_whole_workflow() -> None:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    queue = _Queue()
    capacity = SQLAlchemyWorkflowRuntimeCapacity(
        engine,
        tenant_limit=1,
        worker_limit=1,
    )
    guarded = CapacityGuardedWorkflowAdapterQueue(queue, capacity)
    guarded.submit(
        _submission(
            workflow_id="shared-workflow",
            run_id="first-run",
            step_id="a",
        )
    )

    assert capacity.available_slots(
        tenant_id="tenant-a",
        workflow_id="shared-workflow",
        run_id="first-run",
    ) == 1
    assert capacity.available_slots(
        tenant_id="tenant-a",
        workflow_id="shared-workflow",
        run_id="second-run",
    ) == 0
