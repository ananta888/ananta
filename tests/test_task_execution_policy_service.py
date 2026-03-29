from agent.models import TaskExecutionPolicyContract, TaskStepExecuteRequest
from agent.services.task_execution_policy_service import (
    classify_execution_failure,
    compute_execution_retry_delay,
    resolve_execution_policy,
    should_retry_execution,
)


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


def test_resolve_execution_policy_exposes_extended_retry_settings():
    policy = resolve_execution_policy(
        TaskStepExecuteRequest(),
        agent_cfg={
            "command_timeout": 30,
            "command_retries": 4,
            "command_retry_delay": 2,
            "command_retry_strategy": "exponential",
            "command_max_retry_delay": 9,
            "command_retry_jitter_factor": 0.25,
            "command_retryable_exit_codes": [2, 7],
            "command_retry_on_timeouts": False,
        },
        source="task_execute",
    )

    assert policy.retry_backoff_strategy == "exponential"
    assert policy.max_retry_delay_seconds == 9
    assert policy.jitter_factor == 0.25
    assert policy.retryable_exit_codes == [2, 7]
    assert policy.retry_on_timeouts is False


def test_classify_execution_failure_distinguishes_timeout_and_runtime():
    assert classify_execution_failure(0, "ok") == "success"
    assert classify_execution_failure(-1, "[Error: Timeout]") == "timeout"
    assert classify_execution_failure(-1, "shell crashed") == "command_runtime_error"
    assert classify_execution_failure(17, "failed") == "non_zero_exit_code"


def test_should_retry_execution_respects_policy_for_timeout_and_exit_codes():
    timeout_policy = TaskExecutionPolicyContract(
        timeout_seconds=10,
        retries=2,
        retry_delay_seconds=1,
        source="test",
        retry_on_timeouts=False,
    )
    assert should_retry_execution(exit_code=-1, output="[Error: Timeout]", policy=timeout_policy) is False

    exit_policy = TaskExecutionPolicyContract(
        timeout_seconds=10,
        retries=2,
        retry_delay_seconds=1,
        source="test",
        retryable_exit_codes=[5],
    )
    assert should_retry_execution(exit_code=5, output="bad", policy=exit_policy) is True
    assert should_retry_execution(exit_code=6, output="bad", policy=exit_policy) is False


def test_compute_execution_retry_delay_supports_constant_and_exponential():
    constant_policy = TaskExecutionPolicyContract(
        timeout_seconds=10,
        retries=2,
        retry_delay_seconds=3,
        source="test",
        max_retry_delay_seconds=20,
    )
    assert compute_execution_retry_delay(policy=constant_policy, attempt=2) == 3.0

    exponential_policy = TaskExecutionPolicyContract(
        timeout_seconds=10,
        retries=2,
        retry_delay_seconds=2,
        source="test",
        retry_backoff_strategy="exponential",
        max_retry_delay_seconds=5,
        jitter_factor=0.0,
    )
    assert compute_execution_retry_delay(policy=exponential_policy, attempt=3) == 5.0
