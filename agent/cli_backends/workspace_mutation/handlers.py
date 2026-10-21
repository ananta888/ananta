"""Per-kind action handlers of the workspace-mutation loop.

Each handler consumes one parsed model message and returns either a final
``(rc, out, err)`` (the loop ends) or ``None`` (the loop continues with the
collected evidence). ``ACTION_HANDLERS`` maps message kinds to handlers so
new kinds are added by registration instead of growing the loop (OCP).
"""
from __future__ import annotations

import pathlib
from typing import Any, Callable

from agent.cli_backends.tool_loop import (
    KIND_CANNOT_CONTINUE,
    KIND_FINAL_ANSWER,
    KIND_NEEDS_APPROVAL,
    KIND_TOOL_REQUEST,
)
from agent.cli_backends.workspace_mutation.audit_events import emit_final_answer_blocked
from agent.cli_backends.workspace_mutation.session import LoopResult, LoopTurn, MutationSession
from agent.cli_backends.workspace_mutation.tool_actions import (
    KIND_PATCH_REQUEST,
    block_unanalyzed_mutation,
    handle_patch_request,
    handle_tool_request,
)
from agent.cli_backends.workspace_mutation.tools import (
    build_tool_result,
    generated_source_decision_blocked,
    resolve_workspace_path,
    workspace_path_error_type,
)

KIND_WORKSPACE_WRITE = "workspace_write"


ActionHandler = Callable[[MutationSession, LoopTurn], "LoopResult | None"]


FINAL_ANSWER_CORRECTION_BUDGET = 2


def handle_final_answer(session: MutationSession, turn: LoopTurn) -> "LoopResult | None":
    if session.cfg.get("require_code_analysis") is True and session.analysis_gate.pending:
        # A premature final_answer is correctable first: the loop stays alive for a
        # bounded number of correction turns and shows the required sequence again;
        # only after the budget does it end with the bounded prerequisite failure.
        from agent.cli_backends.workspace_mutation.prompts import ANALYSIS_CORRECTION_NUDGE

        refusals = int(getattr(session, "final_answer_refusals", 0)) + 1
        session.final_answer_refusals = refusals
        turn.row["result"] = "code_analysis_required"
        session.add_evidence(
            {
                "kind": "code_analysis_result",
                "status": "policy_blocked",
                "error": "code_analysis_required",
                "data": session.analysis_gate.state(),
            }
        )
        if refusals <= FINAL_ANSWER_CORRECTION_BUDGET:
            session.correction = ANALYSIS_CORRECTION_NUDGE
            return None
        return session.finish_with_summary(
            "code_analysis_required",
            {"kind": "final_answer_blocked", "reason": "code_analysis_required",
             "code_analysis": session.analysis_gate.state()},
            turn.err,
        )
    final_check = session.hub_check(iteration_number=turn.iteration)
    policy_status = str((final_check.get("policy_result") or {}).get("status") or "ok")
    source_line_status = str((final_check.get("source_line_policy_result") or {}).get("status") or "ok")
    answer = str(turn.message.get("answer") or turn.out)
    if policy_status == "ok" and source_line_status != "blocked":
        return session.finish("final_answer", 0, answer, turn.err)
    # ALWA-015: workspace_mutation_evaluated already fired inside hub_check; this is the *blocked* signal.
    emit_final_answer_blocked(
        final_check=final_check,
        policy_status=policy_status,
        source_line_status=source_line_status,
        task_id=session.task_id,
        iteration_number=turn.iteration,
        mode=session.mode,
    )
    summary = {
        "kind": "final_answer_blocked",
        "status": "policy_blocked",
        "answer": answer,
        "policy_result": final_check.get("policy_result"),
        "source_line_policy_result": final_check.get("source_line_policy_result"),
    }
    return session.finish_with_summary("policy_blocked", summary, turn.err)


def handle_stop_request(session: MutationSession, turn: LoopTurn) -> LoopResult:
    """needs_approval / cannot_continue end the loop; approvals are registered with the hub."""
    kind = str(turn.message.get("kind"))
    reason = str(turn.message.get("reason") or "")
    turn.row["reason"] = reason
    summary: dict[str, Any] = {"kind": kind, "reason": reason}
    if kind == KIND_NEEDS_APPROVAL:
        from agent.cli_backends.tool_loop import register_pending_approval_request

        request_id = register_pending_approval_request(
            task_id=session.task_id,
            tool_name=str(turn.message.get("tool_name") or "worker.needs_approval"),
            arguments=dict(turn.message.get("arguments") or {}),
            reason=reason,
        )
        if request_id:
            summary["approval_request_id"] = request_id
    return session.finish_with_summary(kind, summary, turn.err)


def _restore_file(target: pathlib.Path, existed_before: bool, original_text: str | None) -> None:
    if existed_before and original_text is not None:
        target.write_text(original_text, encoding="utf-8")
        return
    try:
        target.unlink()
    except FileNotFoundError:
        pass


def _write_one_file(
    session: MutationSession, row: dict[str, Any], source_line_results: list[dict[str, Any]]
) -> tuple[str, str | None]:
    """Apply one ``workspace_write`` file; returns (path, rejection reason or None)."""
    rel = str((row or {}).get("path") or "").strip()
    content = (row or {}).get("content")
    if session.count_file_attempt(rel) > session.limits.max_attempts_per_file:
        return rel, "max_patch_attempts_per_file_exceeded"
    if not isinstance(content, str):
        return rel, "content_must_be_text"
    forbidden = session.services.mutation_policy._is_forbidden(rel)
    if forbidden:
        return rel, forbidden
    try:
        target = resolve_workspace_path(session.workspace, rel)
    except workspace_path_error_type() as exc:
        return rel, str(exc)
    target.parent.mkdir(parents=True, exist_ok=True)
    existed_before = target.exists()
    original_text = (
        target.read_text(encoding="utf-8", errors="replace") if existed_before and target.is_file() else None
    )
    baseline = {rel: len(original_text.splitlines()) if original_text is not None else None}
    target.write_text(content, encoding="utf-8")
    source_line_result = session.evaluate_source_lines([rel], baseline).as_dict()
    source_line_results.append(source_line_result)
    if source_line_result.get("status") == generated_source_decision_blocked():
        _restore_file(target, existed_before, original_text)
        return rel, "source_line_policy_blocked"
    return rel, None


def handle_workspace_write(session: MutationSession, turn: LoopTurn) -> LoopResult | None:
    if block_unanalyzed_mutation(session, turn):
        return None
    if session.mode != "controlled_workspace":
        turn.row["result"] = "rejected_direct_write_in_strict_mode"
        session.add_evidence(
            build_tool_result(
                tool_name="workspace_write",
                tool_call_id=f"mutation:{turn.iteration}",
                status="policy_blocked",
                risk_class="write",
                error="direct_write_not_allowed_in_strict_patch_request",
            )
        )
        return None
    applied: list[str] = []
    rejected: list[dict[str, str]] = []
    source_line_results: list[dict[str, Any]] = []
    for row in list(turn.message.get("files") or []):
        rel, reason = _write_one_file(session, row, source_line_results)
        if reason is None:
            applied.append(rel)
        else:
            rejected.append({"path": rel, "reason": reason})
    if any(row["reason"] == "max_patch_attempts_per_file_exceeded" for row in rejected) and not applied:
        turn.row["result"] = "max_patch_attempts_per_file"
        summary = {"kind": "loop_aborted", "reason": "max_patch_attempts_per_file", "rejected": rejected}
        return session.finish_with_summary("max_patch_attempts_per_file", summary, turn.err)
    check = session.hub_check(iteration_number=turn.iteration)
    check["write_result"] = {"applied": applied, "rejected": rejected}
    if source_line_results:
        check["write_source_line_policy_results"] = source_line_results
    turn.row["applied"] = applied
    turn.row["rejected"] = rejected
    turn.row["source_line_policy_statuses"] = [str(row.get("status") or "") for row in source_line_results]
    if applied:
        session.record_source_change_feedback(check)
    else:
        session.add_evidence(check)
    if session.no_progress_detected(check, applied=bool(applied)):
        summary = {"kind": "loop_aborted", "reason": "no_progress_detected"}
        return session.finish_with_summary("no_progress_detected", summary, turn.err)
    return None


def _rejection_hint(reason: str, state: dict) -> str:
    """Machine-readable next step for a rejected analysis, so the model acts instead of guessing."""
    if reason == "code_analysis_source_not_observed":
        return ("cite_an_observed_source_range_with_its_sha256" if state.get("observed_sources")
                else "read_a_source_file_range_first")
    if reason == "codecompass_inspection_required":
        return "call_codecompass_first"
    return ""


def handle_code_analysis(session: MutationSession, turn: LoopTurn) -> None:
    payload = {key: value for key, value in turn.message.items() if key != "kind"}
    reason = session.analysis_gate.accept(payload)
    turn.row["analysis_status"] = reason or "accepted"
    data: dict = dict(session.analysis_gate.state())
    if reason:
        hint = _rejection_hint(reason, data)
        if hint:
            data["action_required"] = hint
    session.add_evidence({"kind": "code_analysis_result", "status": "error" if reason else "ok",
                          "error": reason, "data": data})


ACTION_HANDLERS: dict[str, ActionHandler] = {
    "code_analysis": handle_code_analysis,
    KIND_FINAL_ANSWER: handle_final_answer,
    KIND_NEEDS_APPROVAL: handle_stop_request,
    KIND_CANNOT_CONTINUE: handle_stop_request,
    KIND_WORKSPACE_WRITE: handle_workspace_write,
    KIND_PATCH_REQUEST: handle_patch_request,
    KIND_TOOL_REQUEST: handle_tool_request,
}
