from __future__ import annotations

from agent.models import TaskExecutionPolicyContract, TaskStepExecuteRequest


def resolve_execution_policy(
    request_data: TaskStepExecuteRequest,
    *,
    agent_cfg: dict | None = None,
    source: str = "task_execute",
) -> TaskExecutionPolicyContract:
    agent_cfg = agent_cfg or {}
    default_timeout = max(1, int(agent_cfg.get("command_timeout") or 60))
    default_retries = max(0, int(agent_cfg.get("command_retries") or 0))
    default_retry_delay = max(0, int(agent_cfg.get("command_retry_delay") or 1))

    timeout = max(1, min(int(request_data.timeout or default_timeout), 3600))
    retries = max(0, min(int(request_data.retries if request_data.retries is not None else default_retries), 10))
    retry_delay = max(
        0,
        min(int(request_data.retry_delay if request_data.retry_delay is not None else default_retry_delay), 60),
    )
    return TaskExecutionPolicyContract(
        timeout_seconds=timeout,
        retries=retries,
        retry_delay_seconds=retry_delay,
        source=source,
    )
