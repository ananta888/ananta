"""Hub-validated tool actions of the workspace-mutation loop.

``patch_request`` (AWWPI-015) and ``tool_request`` messages are evaluated by
the hub tool policy before execution; denied calls become evidence and
``approval_required`` decisions are registered as pending approvals (never
waited on here).
"""
from __future__ import annotations

from typing import Any

from agent.cli_backends.workspace_mutation.session import LoopResult, LoopTurn, MutationSession
from agent.cli_backends.workspace_mutation.tools import build_tool_result, execute_ananta_tool

KIND_PATCH_REQUEST = "patch_request"

_MAX_MATERIALIZED_CONTEXT_REFS = 4


def build_patch_tool_call(message: dict[str, Any], rel: str) -> tuple[str, dict[str, Any]]:
    """Translate a ``patch_request`` message into the hub tool call (name, arguments)."""
    variant = str(message.get("variant") or "unified_diff").strip().lower()
    if variant == "unified_diff":
        return "repo.apply_patch", {
            "target_path": rel,
            "variant": "unified_diff",
            "unified_diff": str(message.get("unified_diff") or ""),
            "expected_old_hash": str(message.get("expected_old_hash") or ""),
            "reason": str(message.get("reason") or ""),
        }
    if variant == "replace_range":
        replacement = message.get("replacement") if message.get("replacement") is not None else message.get("content")
        return "repo.apply_patch", {
            "target_path": rel,
            "variant": "replace_range",
            "line_start": int(message.get("line_start") or 0),
            "line_end": int(message.get("line_end") or 0),
            "replacement": str(replacement or ""),
            "expected_old_hash": str(message.get("expected_old_hash") or ""),
            "reason": str(message.get("reason") or ""),
        }
    return "repo.write_file", {
        "path": rel,
        "content": str(message.get("content") or ""),
        "mode": "create_only" if variant == "write_file_create_only" else "replace_existing",
        "expected_old_hash": str(message.get("expected_old_hash") or ""),
    }


def _evaluate_tool_policy(session: MutationSession, turn: LoopTurn, tool_name: str, arguments: dict[str, Any]) -> Any:
    decision = session.services.tool_policy.evaluate(
        tool_name=tool_name,
        arguments=arguments,
        allowed_tools=None,
        mutation_mode=session.mode,
        task_id=session.task_id,
    )
    turn.row["tool_name"] = tool_name
    turn.row["policy_decision"] = decision.decision
    return decision


def _denied_tool_result(tool_name: str, tool_call_id: str, decision: Any) -> dict[str, Any]:
    return build_tool_result(
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        status=decision.decision,
        risk_class=decision.risk_class,
        error=decision.reason,
        policy_decision=decision.as_dict(),
    )


def _register_approval(session: MutationSession, tool_name: str, arguments: dict[str, Any], decision: Any) -> Any:
    from agent.cli_backends.tool_loop import register_pending_approval_request

    return register_pending_approval_request(
        task_id=session.task_id,
        tool_name=tool_name,
        arguments=arguments,
        risk_class=decision.risk_class,
        reason=decision.reason,
    )


def handle_patch_request(session: MutationSession, turn: LoopTurn) -> LoopResult | None:
    message = turn.message
    tool_call_id = session.next_tool_call_id("patch_result")
    rel = str(message.get("target_path") or "").strip()
    if session.count_file_attempt(rel) > session.limits.max_attempts_per_file:
        summary = {"kind": "loop_aborted", "reason": "max_patch_attempts_per_file", "path": rel}
        return session.finish_with_summary("max_patch_attempts_per_file", summary, turn.err)
    tool_name, arguments = build_patch_tool_call(message, rel)
    decision = _evaluate_tool_policy(session, turn, tool_name, arguments)
    if not decision.allowed:
        session.add_evidence(_denied_tool_result(tool_name, tool_call_id, decision))
        if decision.decision != "approval_required":
            return None
        request_id = _register_approval(session, tool_name, arguments, decision)
        summary = {"kind": "loop_aborted", "reason": "approval_required", "tool_name": tool_name}
        if request_id:
            summary["approval_request_id"] = request_id
        return session.finish_with_summary("approval_required", summary, turn.err)
    result = execute_ananta_tool(
        tool_name=tool_name,
        arguments=arguments,
        workspace_dir=str(session.workspace),
        tool_call_id=tool_call_id,
        config=session.cfg,
    )
    check = session.hub_check(iteration_number=turn.iteration)
    check["patch_result"] = result
    session.add_evidence(check)
    turn.row["patch_status"] = str(result.get("status") or "")
    return None


def _materialize_context_ranges(
    session: MutationSession, result: dict[str, Any], tool_call_id: str, tool_cfg: dict[str, Any]
) -> None:
    """``codecompass.plan_context``: read the first location refs as line ranges into the result."""
    refs = list(((result.get("data") or {}).get("context_bundle") or {}).get("location_refs") or [])
    materialized = []
    for ref_index, ref in enumerate(refs[:_MAX_MATERIALIZED_CONTEXT_REFS], start=1):
        path = str((ref or {}).get("path") or "").strip()
        if not path:
            continue
        materialized.append(
            execute_ananta_tool(
                tool_name="repo.read_file_range",
                arguments={
                    "path": path,
                    "line_start": int((ref or {}).get("line_start") or 1),
                    "line_end": int((ref or {}).get("line_end") or 1),
                },
                workspace_dir=str(session.workspace),
                tool_call_id=f"{tool_call_id}:range:{ref_index}",
                config=tool_cfg,
            )
        )
    if materialized:
        result["data"] = {**dict(result.get("data") or {}), "materialized_range_results": materialized}


def handle_tool_request(session: MutationSession, turn: LoopTurn) -> LoopResult | None:
    tool_call_id = session.next_tool_call_id("tool_result")
    tool_name = str(turn.message.get("tool_name") or "").strip()
    arguments = dict(turn.message.get("arguments") or {})
    decision = _evaluate_tool_policy(session, turn, tool_name, arguments)
    if not decision.allowed:
        blocked_result = _denied_tool_result(tool_name, tool_call_id, decision)
        if decision.decision == "approval_required":
            request_id = _register_approval(session, tool_name, arguments, decision)
            if request_id:
                blocked_result["approval_request_id"] = request_id
        session.add_evidence(blocked_result)
        return None
    tool_cfg = {**session.cfg, "materialization_manifest": session.materialization_manifest}
    result = execute_ananta_tool(
        tool_name=tool_name,
        arguments=arguments,
        workspace_dir=str(session.workspace),
        tool_call_id=tool_call_id,
        config=tool_cfg,
    )
    if tool_name == "codecompass.plan_context":
        _materialize_context_ranges(session, result, tool_call_id, tool_cfg)
    if tool_name == "test.run":
        check = session.hub_check(iteration_number=turn.iteration, ran_tests_result=dict(result.get("data") or {}))
        check["tool_result"] = result
        session.add_evidence(check)
    else:
        session.add_evidence(result)
    turn.row["tool_status"] = str(result.get("status") or "")
    return None
