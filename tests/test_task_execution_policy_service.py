from agent.models import TaskStepExecuteRequest
from agent.services.task_execution_policy_service import resolve_execution_policy


def test_resolve_execution_policy_clamps_request_values():
    policy = resolve_execution_policy(
        TaskStepExecuteRequest(timeout=99999, retries=99, retry_delay=999),
        agent_cfg={"command_timeout": 45, "command_retries": 1, "command_retry_delay": 2},
        source="task_execute",
    )

    assert policy.timeout_seconds == 3600
    assert policy.retries == 10
    assert policy.retry_delay_seconds == 60
    assert policy.source == "task_execute"


def test_resolve_execution_policy_uses_agent_defaults():
    policy = resolve_execution_policy(
        TaskStepExecuteRequest(),
        agent_cfg={"command_timeout": 75, "command_retries": 2, "command_retry_delay": 3},
        source="execute_step",
    )

    assert policy.timeout_seconds == 75
    assert policy.retries == 2
    assert policy.retry_delay_seconds == 3
