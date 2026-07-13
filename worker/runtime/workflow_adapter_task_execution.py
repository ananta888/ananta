"""Worker-process composition for delegated workflow adapter Hub tasks."""

from __future__ import annotations

from typing import Any

from flask import current_app, has_app_context

from worker.runtime.workflow_adapter_task_consumer import (
    WorkflowAdapterTaskConsumer,
)

_CONSUMER_EXTENSION = "workflow_adapter_task_consumer"


def consume_delegated_workflow_task(task: dict[str, Any]) -> dict[str, Any] | None:
    """Return a route payload for a recognized task, otherwise ``None``."""

    if not WorkflowAdapterTaskConsumer.supports(task):
        return None
    consumer = _consumer()
    result = consumer.consume(task)
    verification = result.verification_update()
    task_status = {
        "success": "completed",
        "cancelled": "cancelled",
    }.get(result.status, "failed")
    return {
        "status": task_status,
        "output": result.summary or result.reason_code,
        "exit_code": 0 if task_status == "completed" else 1,
        "reason_code": result.reason_code,
        "adapter_kind": result.adapter_kind,
        "artifacts": [dict(item) for item in result.artifacts],
        "sources": [dict(item) for item in result.sources],
        "workflow_adapter_verification": verification,
    }


def _consumer() -> WorkflowAdapterTaskConsumer:
    if has_app_context():
        configured = current_app.extensions.get(_CONSUMER_EXTENSION)
        if isinstance(configured, WorkflowAdapterTaskConsumer):
            return configured
    from worker.runtime.workflow_adapter_runtime_composition import (
        build_workflow_adapter_worker_runtime,
    )

    agent_config = (
        dict(current_app.config.get("AGENT_CONFIG") or {})
        if has_app_context()
        else {}
    )
    return build_workflow_adapter_worker_runtime(
        agent_config=agent_config
    ).consumer


__all__ = ["consume_delegated_workflow_task"]
