from __future__ import annotations

from agent.services.worker_contract_service import get_worker_contract_service
from worker.core.verification import validate_worker_schema_payload


def test_build_worker_todo_contract_emits_schema_valid_payload() -> None:
    payload = get_worker_contract_service().build_worker_todo_contract(
        task_id="task-1",
        goal_id="goal-1",
        trace_id="trace-1",
        capability_id="worker.command.execute",
        context_hash="ctx-1",
        executor_kind="ananta_worker",
        worker_profile="balanced",
        track="worker-subplan",
        tasks=[
            {
                "id": "todo-1",
                "title": "Implement patch",
                "description": "Apply requested code patch and validate output.",
                "status": "todo",
                "acceptance": ["Patch artifact returned"],
                "expected_artifacts": [{"kind": "patch_artifact", "required": True}],
            }
        ],
        mode="assistant_execute",
        runner_prompt="Execute delegated todo contract.",
        allowed_tools=["bash", "view"],
    )
    validate_worker_schema_payload(schema_name="worker_todo_contract.v1", payload=payload)
    assert payload["expected_result_schema"] == "worker_todo_result.v1"
    assert payload["todo"]["tasks"][0]["id"] == "todo-1"
