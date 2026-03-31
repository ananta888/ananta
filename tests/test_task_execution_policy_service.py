from agent.models import TaskExecutionPolicyContract, TaskStepExecuteRequest
from agent.services.task_execution_policy_service import (
    classify_execution_failure,
    compute_execution_retry_delay,
    normalize_allowed_tools,
    resolve_execution_policy,
    resolve_task_scope_allowed_tools,
    should_retry_execution,
    validate_task_scoped_tool_calls,
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


def test_resolve_execution_policy_prefers_task_kind_defaults():
    policy = resolve_execution_policy(
        TaskStepExecuteRequest(task_kind="ops"),
        agent_cfg={
            "command_timeout": 30,
            "command_retries": 0,
            "command_retry_delay": 1,
            "task_kind_execution_policies": {
                "ops": {
                    "command_timeout": 120,
                    "command_retries": 3,
                    "command_retry_delay": 5,
                    "command_retry_strategy": "exponential",
                }
            },
        },
        source="task_execute",
    )

    assert policy.timeout_seconds == 120
    assert policy.retries == 3
    assert policy.retry_delay_seconds == 5
    assert policy.retry_backoff_strategy == "exponential"
    assert policy.source == "task_execute:ops"


def test_resolve_execution_policy_applies_override_without_hiding_explicit_fields():
    policy = resolve_execution_policy(
        TaskStepExecuteRequest(
            task_kind="coding",
            retries=4,
            retry_policy_override={
                "timeout_seconds": 77,
                "retries": 2,
                "retry_backoff_strategy": "exponential",
                "retryable_exit_codes": [9],
            },
        ),
        agent_cfg={"command_timeout": 30, "command_retries": 1, "command_retry_delay": 1},
        source="execute_step",
    )

    assert policy.timeout_seconds == 77
    assert policy.retries == 4
    assert policy.retry_backoff_strategy == "exponential"
    assert policy.retryable_exit_codes == [9]


def test_normalize_allowed_tools_strips_deduplicates_and_drops_empty_values():
    assert normalize_allowed_tools([" list_teams ", "", "list_teams", None, "create_team"]) == ["list_teams", "create_team"]


def test_resolve_task_scope_allowed_tools_reads_worker_execution_context():
    task = {"worker_execution_context": {"allowed_tools": [" list_teams ", "list_teams", "create_team"]}}
    assert resolve_task_scope_allowed_tools(task) == ["list_teams", "create_team"]


def test_validate_task_scoped_tool_calls_rejects_unknown_and_out_of_scope_tools():
    blocked, reasons = validate_task_scoped_tool_calls(
        [{"name": "create_team", "args": {}}, {"name": "missing_tool", "args": {}}],
        allowed_tools=["list_teams"],
        known_tools=["list_teams", "create_team"],
    )
    assert blocked == ["create_team", "missing_tool"]
    assert reasons["create_team"] == "tool_not_allowed_for_task_scope"
    assert reasons["missing_tool"] == "unknown_tool"


def test_validate_task_scoped_tool_calls_keeps_existing_unscoped_flows_compatible():
    blocked, reasons = validate_task_scoped_tool_calls(
        [{"name": "list_teams", "args": {}}],
        allowed_tools=[],
        known_tools=["list_teams", "create_team"],
    )
    assert blocked == []
    assert reasons == {}
