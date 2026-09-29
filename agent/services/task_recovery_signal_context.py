"""Pure inputs of a recovery proposal: the exhaustion signal and the bounded context.

Split out of ``task_recovery_planning_service`` (SRP): summarizing the
worker-reported ``model_recovery_signal.v1`` facts into one verified exhaustion
signal, and compacting the source task into a bounded planning context. Both
functions depend only on their arguments; the proposal step imports them
directly and ``TaskRecoveryPlanningService`` keeps its historic
``_safe_signal_summary`` / ``_compacted_context`` names as aliases.
"""

from __future__ import annotations

import json
from typing import Any

from agent.services.task_recovery_planning_rules import (
    RECOVERY_SIGNAL_SCHEMA,
    _record_recovery_cut,
    _recovery_context_chars,
)
from agent.services.task_recovery_planning_values import mapping as _mapping


def summarize_exhaustion_signal(
    strategy_failures: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    failure_types: set[str] = set()
    error_types: set[str] = set()
    profile_ids: set[str] = set()
    model_ids: set[str] = set()
    attempt_count = 0
    structured_signal_seen = False
    non_recoverable_terminal_seen = False

    from ananta_contracts.model_recovery import (
        NON_RECOVERABLE_TERMINAL_REASONS,
        is_recoverable_model_error_type,
        sanitize_terminal_model_recovery_signal,
    )

    for failure in list(strategy_failures or []):
        if not isinstance(failure, dict):
            continue
        failure_type = str(failure.get("failure_type") or "").strip().lower()
        if failure_type:
            failure_types.add(failure_type)
            if failure_type in NON_RECOVERABLE_TERMINAL_REASONS:
                non_recoverable_terminal_seen = True
        model_id = str(failure.get("model") or "").strip()
        if model_id:
            model_ids.add(model_id[:160])
        metadata = failure.get("metadata") if isinstance(failure.get("metadata"), dict) else {}
        fallback_decisions = failure.get("fallback_decisions")
        if not isinstance(fallback_decisions, list):
            fallback_decisions = metadata.get("fallback_decisions")
        for decision in fallback_decisions if isinstance(fallback_decisions, list) else []:
            if not isinstance(decision, dict) or not bool(decision.get("terminal")):
                continue
            trigger = str(decision.get("trigger") or "").strip().lower()
            if not is_recoverable_model_error_type(trigger):
                non_recoverable_terminal_seen = True
        signal = failure.get("model_recovery_signal")
        if not isinstance(signal, dict):
            signal = metadata.get("model_recovery_signal")
        if not isinstance(signal, dict):
            continue
        raw_terminal_reason = str(signal.get("terminal_reason") or "").strip().lower()
        if not is_recoverable_model_error_type(raw_terminal_reason):
            non_recoverable_terminal_seen = True

        signal = sanitize_terminal_model_recovery_signal(signal)
        if signal is None:
            non_recoverable_terminal_seen = True
            continue
        structured_signal_seen = True
        attempt_count += max(0, int(signal.get("attempt_count") or 0))
        for value in list(signal.get("error_types") or []):
            normalized = str(value or "").strip().lower()
            if normalized:
                error_types.add(normalized[:80])
        for value in list(signal.get("failed_profile_ids") or []):
            normalized = str(value or "").strip()
            if normalized:
                profile_ids.add(normalized[:160])
        reason_code = str(signal.get("reason_code") or "").strip().lower()
        if reason_code:
            failure_types.add(reason_code[:80])
        terminal_reason = str(signal.get("terminal_reason") or "").strip().lower()
        if terminal_reason:
            failure_types.add(terminal_reason[:80])

    if non_recoverable_terminal_seen or not structured_signal_seen:
        return None
    return {
        "schema": RECOVERY_SIGNAL_SCHEMA,
        "reason_code": "model_or_strategy_exhausted",
        "terminal": True,
        "attempt_count": max(attempt_count, len(list(strategy_failures or []))),
        "failure_types": sorted(failure_types),
        "error_types": sorted(error_types),
        "failed_profile_ids": sorted(profile_ids),
        "failed_models": sorted(model_ids),
    }


def compact_recovery_context(task: Any, *, actions: list[str]) -> tuple[str, dict[str, Any]]:
    data = _mapping(task)
    title = str(data.get("title") or "").strip()
    description = str(data.get("description") or "").strip()
    execution_context = _mapping(data.get("worker_execution_context"))
    context_data = _mapping(execution_context.get("context"))
    context_text = str(context_data.get("context_text") or "")
    if "compact_context" not in actions:
        bounded = "\n".join(value for value in (title, description, context_text) if value)
        limit = _recovery_context_chars()
        _record_recovery_cut("recovery.context", bounded, limit)
        return bounded[:limit], {
            "status": "bounded_without_compactor",
            "input_chars": len(title) + len(description) + len(context_text),
            "output_chars": min(limit, len(bounded)),
        }

    from agent.services.planning_context_compactor_service import (
        get_planning_context_compactor_service,
    )
    from agent.services.propose_policy import ProposePolicy

    compacted = get_planning_context_compactor_service().compact(
        goal_text=title or description or "Recover delegated task",
        context_text="\n".join(value for value in (description, context_text) if value),
        mode="generic",
        mode_data={"recovery": True, "segment_planning": "segment_planning" in actions},
        planning_policy={},
        llm_config={},
        policy=ProposePolicy(
            context_compaction_enabled=False,
            context_compaction_required=False,
            context_compactor_max_output_chars=_recovery_context_chars(),
            context_compactor_retry_attempts=0,
            context_compactor_fail_open=True,
        ),
    )
    payload = dict(compacted.payload or {})
    payload.pop("compactor_meta", None)
    compact_json = json.dumps(payload, ensure_ascii=False)
    _record_recovery_cut("recovery.context", compact_json, _recovery_context_chars())
    return compact_json[:_recovery_context_chars()], {
        key: value
        for key, value in dict(compacted.meta or {}).items()
        if key
        in {
            "input_chars",
            "output_chars",
            "reduction_ratio",
            "truncated_fields",
            "status",
            "error_classification",
            "fallback_stage",
        }
    }
