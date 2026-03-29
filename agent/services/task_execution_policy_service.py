from __future__ import annotations

from random import random

from agent.models import TaskExecutionPolicyContract, TaskStepExecuteRequest
from agent.runtime_policy import normalize_task_kind


def _task_kind_policy(agent_cfg: dict | None, task_kind: str | None) -> dict:
    task_kind_policies = (agent_cfg or {}).get("task_kind_execution_policies", {}) or {}
    if not task_kind:
        return {}
    candidate = task_kind_policies.get(task_kind)
    return dict(candidate) if isinstance(candidate, dict) else {}


def _policy_value(
    override_cfg: dict,
    task_kind_cfg: dict,
    agent_cfg: dict,
    *keys: str,
    default,
):
    for source in (override_cfg, task_kind_cfg, agent_cfg):
        for key in keys:
            if key in source and source.get(key) is not None:
                return source.get(key)
    return default


def resolve_execution_policy(
    request_data: TaskStepExecuteRequest,
    *,
    agent_cfg: dict | None = None,
    source: str = "task_execute",
) -> TaskExecutionPolicyContract:
    agent_cfg = agent_cfg or {}
    explicit_fields = set(getattr(request_data, "model_fields_set", set()) or set())
    task_kind = normalize_task_kind(getattr(request_data, "task_kind", None), getattr(request_data, "command", None) or "")
    override_cfg = dict(request_data.retry_policy_override or {}) if isinstance(request_data.retry_policy_override, dict) else {}
    task_kind_cfg = _task_kind_policy(agent_cfg, task_kind)

    default_timeout = max(
        1,
        int(_policy_value(override_cfg, task_kind_cfg, agent_cfg, "timeout", "timeout_seconds", "command_timeout", default=60)),
    )
    default_retries = max(
        0,
        int(_policy_value(override_cfg, task_kind_cfg, agent_cfg, "retries", "command_retries", default=0)),
    )
    default_retry_delay = max(
        0,
        int(
            _policy_value(
                override_cfg,
                task_kind_cfg,
                agent_cfg,
                "retry_delay",
                "retry_delay_seconds",
                "command_retry_delay",
                default=1,
            )
        ),
    )
    retry_strategy = (
        str(
            _policy_value(
                override_cfg,
                task_kind_cfg,
                agent_cfg,
                "retry_backoff_strategy",
                "retry_strategy",
                "command_retry_strategy",
                default="constant",
            )
            or "constant"
        )
        .strip()
        .lower()
    )
    if retry_strategy not in {"constant", "exponential"}:
        retry_strategy = "constant"
    max_retry_delay = max(
        0,
        min(
            int(
                _policy_value(
                    override_cfg,
                    task_kind_cfg,
                    agent_cfg,
                    "max_retry_delay_seconds",
                    "max_retry_delay",
                    "command_max_retry_delay",
                    default=60,
                )
                or 60
            ),
            300,
        ),
    )
    jitter_factor = max(
        0.0,
        min(
            float(
                _policy_value(
                    override_cfg,
                    task_kind_cfg,
                    agent_cfg,
                    "jitter_factor",
                    "command_retry_jitter_factor",
                    default=0.0,
                )
                or 0.0
            ),
            1.0,
        ),
    )
    retryable_exit_codes = [
        int(code)
        for code in list(
            _policy_value(
                override_cfg,
                task_kind_cfg,
                agent_cfg,
                "retryable_exit_codes",
                "command_retryable_exit_codes",
                default=[1, -1],
            )
            or [1, -1]
        )
        if str(code).strip()
    ]
    retry_on_timeouts = bool(
        _policy_value(
            override_cfg,
            task_kind_cfg,
            agent_cfg,
            "retry_on_timeouts",
            "command_retry_on_timeouts",
            default=True,
        )
    )

    timeout_value = request_data.timeout if "timeout" in explicit_fields else default_timeout
    retries_value = request_data.retries if "retries" in explicit_fields else default_retries
    retry_delay_value = request_data.retry_delay if "retry_delay" in explicit_fields else default_retry_delay

    timeout = max(1, min(int(timeout_value or default_timeout), 3600))
    retries = max(0, min(int(retries_value if retries_value is not None else default_retries), 10))
    retry_delay = max(
        0,
        min(int(retry_delay_value if retry_delay_value is not None else default_retry_delay), 60),
    )
    resolved_source = source
    if getattr(request_data, "task_kind", None):
        resolved_source = f"{source}:{task_kind}"

    return TaskExecutionPolicyContract(
        timeout_seconds=timeout,
        retries=retries,
        retry_delay_seconds=retry_delay,
        source=resolved_source,
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
