from __future__ import annotations

from typing import Any

from agent.services.unsloth_task_port import HubUnslothTaskSubmissionAdapter


class _Queue:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def ingest_task(self, **values: Any) -> None:
        self.calls.append(dict(values))


class _Repository:
    def get_by_id(self, _task_id: str) -> None:
        return None


def test_stop_training_task_is_hub_owned_and_capability_bound() -> None:
    queue = _Queue()
    adapter = HubUnslothTaskSubmissionAdapter(
        task_queue=queue,
        task_repository=_Repository(),
    )

    task_id = adapter.submit(
        task_type="unsloth.mcp.stop_training",
        tenant_id="tenant-a",
        payload={
            "schema": "ananta.unsloth_hub_task_command.v1",
            "actor_id": "admin-a",
            "arguments": {"save": True},
            "correlation_id": "correlation-stop-0001",
        },
        idempotency_key="idempotency-stop-0001",
    )

    assert task_id.startswith("unsloth-")
    admitted = queue.calls[0]["extra_fields"]
    assert admitted["required_capabilities"] == ["unsloth_mcp_control"]
    context = admitted["worker_execution_context"]["unsloth_task"]
    assert context["result_handler"] == "unsloth_mcp_control_v1"
    assert context["followup_task_creation_allowed"] is False
