from __future__ import annotations

import re
from dataclasses import dataclass

from agent.utils import _extract_command, _extract_reason, _extract_tool_calls


@dataclass(frozen=True)
class LocalExecutionResult:
    output: str
    exit_code: int | None
    retries_used: int
    failure_type: str
    retry_history: list[dict]
    status: str
    loop_signals: list[dict]
    loop_detection: dict | None
    approval_decision: dict | None


def resolve_loop_trace_id(task: dict) -> str | None:
    trace_id = str(task.get("goal_trace_id") or "").strip()
    if trace_id:
        return trace_id
    proposal_trace = (task.get("last_proposal") or {}).get("trace") if isinstance(task.get("last_proposal"), dict) else {}
    trace_id = str((proposal_trace or {}).get("trace_id") or "").strip()
    return trace_id or None


def loop_signature(value: str | None) -> str | None:
    signature = str(value or "").strip()
    if not signature:
        return None
    return signature[:260]


def build_proposal_payload(raw_response: str) -> dict:
    reason = _extract_reason(raw_response)
    command = _extract_command(raw_response)
    tool_calls = _extract_tool_calls(raw_response)
    return {
        "reason": reason,
        "command": command if command and command != raw_response.strip() else None,
        "tool_calls": tool_calls,
        "raw": raw_response,
    }


def repair_command_transcription_noise(command: str) -> str:
    repaired = str(command or "")
    join_fragment_pattern = re.compile(r"(?<=[A-Za-z0-9_./-])>\s+(?=[A-Za-z0-9_./-])")
    while True:
        next_value, count = join_fragment_pattern.subn("", repaired)
        repaired = next_value
        if count <= 0:
            break
    return repaired.strip()


def is_recoverable_missing_binary_failure(*, command: str | None, output: str | None, exit_code: int | None) -> bool:
    if not command or int(exit_code or 0) == 0:
        return False
    text = str(output or "").lower()
    if "command not found" not in text:
        return False
    return True


def is_non_fatal_tool_error(*, tool_name: str | None, error_text: str | None) -> bool:
    name = str(tool_name or "").strip().lower()
    text = str(error_text or "").strip().lower()
    if name == "web_search" and "action pack 'browser'" in text and "deaktiviert" in text:
        return True
    return False


def normalize_runtime_tool_calls(tool_calls: list[dict] | None) -> list[dict]:
    from agent.services.task_execution_policy_service import normalize_tool_call_name

    normalized: list[dict] = []
    for item in list(tool_calls or []):
        if not isinstance(item, dict):
            continue
        tc = dict(item)
        raw_name = str(tc.get("name") or tc.get("tool_name") or "").strip()
        args = tc.get("args") or tc.get("tool_input") or tc.get("parameters") or {}
        if not isinstance(args, dict):
            args = {}
        canonical = normalize_tool_call_name(raw_name)
        if canonical:
            tc["name"] = canonical
            tc["tool_name"] = canonical
        if "command" not in args and args.get("cmd"):
            args["command"] = args.get("cmd")
        tc["args"] = args
        normalized.append(tc)
    return normalized


def approval_call_identity(*, command: str | None, tool_calls: list[dict] | None) -> tuple[str, dict]:
    first_tool = ""
    for item in list(tool_calls or []):
        if isinstance(item, dict):
            first_tool = str(item.get("name") or item.get("tool_name") or "").strip()
            if first_tool:
                break
    tool_name = first_tool or ("shell.command" if str(command or "").strip() else "task.step")
    return tool_name, {"command": str(command or ""), "tool_calls": list(tool_calls or [])}


def apply_implicit_execution_defaults(execution_policy, request_data, agent_cfg: dict) -> None:
    explicit_fields = set(getattr(request_data, "model_fields_set", set()) or set())
    if "retries" not in explicit_fields and agent_cfg.get("command_retries") is not None:
        execution_policy.retries = max(0, min(int(agent_cfg.get("command_retries") or 0), 10))
    if "retry_delay" not in explicit_fields and agent_cfg.get("command_retry_delay") is not None:
        execution_policy.retry_delay_seconds = max(0, min(int(agent_cfg.get("command_retry_delay") or 0), 60))
    if request_data.retry_policy_override is None and agent_cfg.get("command_retryable_exit_codes") is not None:
        execution_policy.retryable_exit_codes = [int(code) for code in list(agent_cfg.get("command_retryable_exit_codes") or [])]
    if request_data.retry_policy_override is None and agent_cfg.get("command_retry_on_timeouts") is not None:
        execution_policy.retry_on_timeouts = bool(agent_cfg.get("command_retry_on_timeouts"))


def build_cli_result_contract(cli_result: dict | None):
    from agent.models import TaskCliResultContract

    if not isinstance(cli_result, dict):
        return None
    return TaskCliResultContract.model_validate(cli_result)


def build_routing_contract(routing: dict | None):
    from agent.models import TaskRoutingContract

    if not isinstance(routing, dict):
        return None
    return TaskRoutingContract.model_validate(routing)


def build_review_contract(review: dict | None):
    from agent.models import TaskReviewStateContract

    if not isinstance(review, dict):
        return None
    return TaskReviewStateContract.model_validate(review)


def build_worker_context_contract(worker_context: dict | None):
    from agent.models import TaskWorkerContextSummaryContract

    if not isinstance(worker_context, dict):
        return None
    return TaskWorkerContextSummaryContract.model_validate(worker_context)


def build_research_artifact_contract(research_artifact: dict | None):
    from agent.models import ResearchArtifact

    if not isinstance(research_artifact, dict):
        return None
    return ResearchArtifact.model_validate(research_artifact)


def build_research_context_contract(research_context: dict | None):
    from agent.models import ResearchContextSummaryContract

    if not isinstance(research_context, dict):
        return None
    return ResearchContextSummaryContract.model_validate(research_context)
