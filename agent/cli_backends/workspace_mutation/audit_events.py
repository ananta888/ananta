"""ALWA-014/015 audit emission for the workspace-mutation loop.

The audit facade is imported lazily on every call so that tests patching
``agent.common.audit.audit_workspace_mutation_event`` observe the events.
Audit failures never break the loop (best effort, as before the split).
"""
from __future__ import annotations

import hashlib
from typing import Any

_VIOLATING_SOURCE_LINE_DECISIONS = {"blocked", "followup_required"}


def _blocked_change_ids(blocked_changes: list[Any]) -> list[str]:
    return [f"{row.get('path','')}:{row.get('reason','')}" for row in blocked_changes if isinstance(row, dict)]


def _source_line_violation_ids(source_line_result: dict[str, Any], decisions: set[str]) -> list[str]:
    return [
        f"{row.get('path','')}:{row.get('reason_code','')}"
        for row in list(source_line_result.get("file_results") or [])
        if isinstance(row, dict) and str(row.get("decision") or "") in decisions
    ]


def emit_evaluation_events(
    *,
    policy_result: Any,
    source_line_result: dict[str, Any],
    diff_text: str,
    changed_paths: list[str],
    task_id: str | None,
    iteration_number: int | None,
    mode: str,
) -> None:
    """``workspace_mutation_evaluated`` plus the generated-source line-policy events."""
    try:
        from agent.common.audit import (
            AUDIT_WORKSPACE_MUTATION_EVALUATED,
            audit_workspace_mutation_event,
        )

        violation_ids = _blocked_change_ids(list(policy_result.blocked_changes or []))
        # diff_hash is computed over the truncated diff_text; file contents are never read here.
        diff_hash = hashlib.sha256(diff_text.encode("utf-8")).hexdigest() if diff_text else None
        source_line_status = str(source_line_result.get("status") or "ok")
        source_line_summary = dict(source_line_result.get("summary") or {})
        audit_workspace_mutation_event(
            AUDIT_WORKSPACE_MUTATION_EVALUATED,
            task_id=task_id,
            iteration_number=iteration_number,
            mutation_mode=mode,
            changed_paths=changed_paths,
            diff_hash=diff_hash,
            policy_decision=str(policy_result.status or "unknown"),
            violation_ids=violation_ids,
            violation_summary="; ".join(violation_ids) or None,
            source_line_policy_status=source_line_status,
            source_line_policy_summary=source_line_summary,
        )
        from agent.common.audit import AUDIT_GENERATED_SOURCE_LINE_POLICY_EVALUATED

        audit_workspace_mutation_event(
            AUDIT_GENERATED_SOURCE_LINE_POLICY_EVALUATED,
            task_id=task_id,
            iteration_number=iteration_number,
            mutation_mode=mode,
            changed_paths=changed_paths,
            policy_decision=source_line_status,
            source_line_policy_summary=source_line_summary,
        )
        if source_line_status in _VIOLATING_SOURCE_LINE_DECISIONS:
            from agent.common.audit import AUDIT_GENERATED_SOURCE_LINE_POLICY_VIOLATION

            audit_workspace_mutation_event(
                AUDIT_GENERATED_SOURCE_LINE_POLICY_VIOLATION,
                task_id=task_id,
                iteration_number=iteration_number,
                mutation_mode=mode,
                changed_paths=changed_paths,
                policy_decision=str(source_line_result.get("status") or "unknown"),
                violation_ids=_source_line_violation_ids(source_line_result, _VIOLATING_SOURCE_LINE_DECISIONS),
                source_line_policy_summary=source_line_summary,
            )
    except Exception:
        pass


def emit_final_answer_blocked(
    *,
    final_check: dict[str, Any],
    policy_status: str,
    source_line_status: str,
    task_id: str | None,
    iteration_number: int,
    mode: str,
) -> None:
    """ALWA-015: the *blocked* signal for a final answer that violates the workspace policy."""
    try:
        from agent.common.audit import (
            AUDIT_WORKSPACE_MUTATION_BLOCKED,
            audit_workspace_mutation_event,
        )

        policy_dict = dict(final_check.get("policy_result") or {})
        source_line_dict = dict(final_check.get("source_line_policy_result") or {})
        blocked_ids = _blocked_change_ids(list(policy_dict.get("blocked_changes") or []))
        violation_ids = blocked_ids + _source_line_violation_ids(source_line_dict, {"blocked"})
        decision = str(source_line_status if source_line_status == "blocked" else policy_status)
        audit_workspace_mutation_event(
            AUDIT_WORKSPACE_MUTATION_BLOCKED,
            task_id=task_id,
            iteration_number=iteration_number,
            mutation_mode=mode,
            changed_paths=list((final_check.get("diff_result") or {}).get("changed_files") or []),
            policy_decision=decision,
            violation_ids=violation_ids,
            violation_summary="; ".join(blocked_ids) or None,
            blocked_reason=decision,
            source_line_policy_status=source_line_status,
            source_line_policy_summary=dict(source_line_dict.get("summary") or {}),
        )
    except Exception:
        pass
