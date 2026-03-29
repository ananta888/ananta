from __future__ import annotations

from random import random

from agent.models import TaskExecutionPolicyContract, TaskStepExecuteRequest


def resolve_execution_policy(
    request_data: TaskStepExecuteRequest,
    *,
    agent_cfg: dict | None = None,
    source: str = "task_execute",
) -> TaskExecutionPolicyContract:
    agent_cfg = agent_cfg or {}
    explicit_fields = set(getattr(request_data, "model_fields_set", set()) or set())
    default_timeout = max(1, int(agent_cfg.get("command_timeout") or 60))
    default_retries = max(0, int(agent_cfg.get("command_retries") or 0))
    default_retry_delay = max(0, int(agent_cfg.get("command_retry_delay") or 1))
    retry_strategy = str(agent_cfg.get("command_retry_strategy") or "constant").strip().lower() or "constant"
    if retry_strategy not in {"constant", "exponential"}:
        retry_strategy = "constant"
    max_retry_delay = max(0, min(int(agent_cfg.get("command_max_retry_delay") or 60), 300))
    jitter_factor = max(0.0, min(float(agent_cfg.get("command_retry_jitter_factor") or 0.0), 1.0))
    retryable_exit_codes = [
        int(code)
        for code in list(agent_cfg.get("command_retryable_exit_codes") or [1, -1])
        if str(code).strip()
    ]
    retry_on_timeouts = bool(agent_cfg.get("command_retry_on_timeouts", True))

    timeout_value = request_data.timeout if "timeout" in explicit_fields else default_timeout
    retries_value = request_data.retries if "retries" in explicit_fields else default_retries
    retry_delay_value = request_data.retry_delay if "retry_delay" in explicit_fields else default_retry_delay

    timeout = max(1, min(int(timeout_value or default_timeout), 3600))
    retries = max(0, min(int(retries_value if retries_value is not None else default_retries), 10))
    retry_delay = max(
        0,
        min(int(retry_delay_value if retry_delay_value is not None else default_retry_delay), 60),
    )
    return TaskExecutionPolicyContract(
        timeout_seconds=timeout,
        retries=retries,
        retry_delay_seconds=retry_delay,
        source=source,
        retry_backoff_strategy=retry_strategy,
        max_retry_delay_seconds=max_retry_delay,
        jitter_factor=jitter_factor,
        retryable_exit_codes=retryable_exit_codes,
        retry_on_timeouts=retry_on_timeouts,
    )


def classify_execution_failure(exit_code: int | None, output: str | None) -> str:
    text = str(output or "")
    if exit_code in {None, 0}:
        return "success"
    if "[Error: Timeout]" in text:
        return "timeout"
    if exit_code == -1:
        return "command_runtime_error"
    return "non_zero_exit_code"


def should_retry_execution(*, exit_code: int | None, output: str | None, policy: TaskExecutionPolicyContract) -> bool:
    failure_type = classify_execution_failure(exit_code, output)
    if failure_type == "success":
        return False
    if failure_type == "timeout":
        return bool(policy.retry_on_timeouts)
    return int(exit_code or 0) in {int(code) for code in list(policy.retryable_exit_codes or [])}


def compute_execution_retry_delay(*, policy: TaskExecutionPolicyContract, attempt: int) -> float:
    if policy.retry_backoff_strategy == "exponential":
        return _compute_backoff_delay_seconds(
            attempt,
            float(policy.retry_delay_seconds),
            max_backoff_seconds=float(policy.max_retry_delay_seconds),
            jitter_factor=float(policy.jitter_factor),
        )
    return float(min(policy.retry_delay_seconds, policy.max_retry_delay_seconds))


def _compute_backoff_delay_seconds(
    attempt: int,
    base_backoff_seconds: float,
    *,
    max_backoff_seconds: float = 30.0,
    jitter_factor: float = 0.2,
) -> float:
    base = max(0.0, float(base_backoff_seconds))
    if base == 0:
        return 0.0
    bounded = min(base * (2 ** max(0, attempt - 1)), max(0.0, float(max_backoff_seconds)))
    jitter = bounded * max(0.0, float(jitter_factor)) * random()
    return bounded + jitter
