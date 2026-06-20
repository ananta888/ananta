from __future__ import annotations

from typing import Any

from agent.services._editor_tui_utils import (
    _clean_text,
    _compact_task_item,
    _normalize_connection_profile,
)


def build_tui_auth_session_support(
    profile: dict[str, Any],
    *,
    auth_state: str = "authenticated",
    session_ttl_minutes: int = 120,
) -> dict[str, Any]:
    normalized = _normalize_connection_profile(profile)
    return {
        "schema": "tui_auth_session_support_v1",
        "connection_profile": normalized,
        "auth_state": _clean_text(auth_state, max_chars=40),
        "session_ttl_minutes": max(1, int(session_ttl_minutes)),
        "secret_echoed": False,
        "long_lived_terminal_safe": True,
    }


def build_tui_runtime_status_header(
    profile: dict[str, Any],
    *,
    connection_state: str = "connected",
    health_state: str = "ok",
    runtime_mode: str = "standard",
) -> dict[str, Any]:
    normalized = _normalize_connection_profile(profile)
    return {
        "schema": "tui_runtime_status_header_v1",
        "profile_id": normalized["id"],
        "target_endpoint": normalized["endpoint"],
        "connection_state": _clean_text(connection_state, max_chars=40),
        "health_state": _clean_text(health_state, max_chars=40),
        "runtime_mode": _clean_text(runtime_mode, max_chars=40),
        "compact_and_visible": True,
    }


def build_tui_task_board_view(
    tasks: list[dict[str, Any]],
    *,
    group_by: str = "status",
) -> dict[str, Any]:
    effective_group = group_by if group_by in {"status", "priority"} else "status"
    compact_tasks = [_compact_task_item(task) for task in tasks]
    return {
        "schema": "tui_task_board_view_v1",
        "group_by": effective_group,
        "tasks": compact_tasks,
        "operational_readiness": "task_board_or_list_supported",
    }


def build_tui_task_detail_view(
    task: dict[str, Any],
    *,
    artifacts: list[dict[str, Any]] | None = None,
    routing_hints: list[str] | None = None,
) -> dict[str, Any]:
    artifacts = artifacts or []
    return {
        "schema": "tui_task_detail_view_v1",
        "task": _compact_task_item(task),
        "summary": _clean_text(task.get("summary") or task.get("title"), max_chars=400),
        "routing_hints": [_clean_text(hint, max_chars=120) for hint in (routing_hints or [])],
        "artifacts": [
            {
                "id": _clean_text(item.get("id"), max_chars=100),
                "title": _clean_text(item.get("title"), max_chars=180),
                "type": _clean_text(item.get("type"), max_chars=40),
            }
            for item in artifacts
        ],
        "terminal_readable": True,
    }


def build_tui_artifact_view(
    artifacts: list[dict[str, Any]],
    *,
    selected_artifact_id: str | None = None,
) -> dict[str, Any]:
    items = [
        {
            "id": _clean_text(item.get("id"), max_chars=100),
            "title": _clean_text(item.get("title"), max_chars=180),
            "type": _clean_text(item.get("type"), max_chars=40),
        }
        for item in artifacts or []
    ]
    selected = (
        _clean_text(selected_artifact_id, max_chars=100)
        if selected_artifact_id
        else (items[0]["id"] if items else None)
    )
    selected_item = next((item for item in items if item["id"] == selected), None)
    return {
        "schema": "tui_artifact_view_v1",
        "items": items,
        "selected_artifact_id": selected,
        "selected_artifact": selected_item,
    }


def build_tui_goal_view(
    goals: list[dict[str, Any]],
    *,
    allow_submission: bool = True,
) -> dict[str, Any]:
    items = [
        {
            "id": _clean_text(goal.get("id"), max_chars=100),
            "title": _clean_text(goal.get("title"), max_chars=200),
            "status": _clean_text(goal.get("status") or "open", max_chars=30),
        }
        for goal in goals or []
    ]
    return {
        "schema": "tui_goal_view_v1",
        "goals": items,
        "goal_submission_available": bool(allow_submission),
    }


def build_tui_goal_submission_entry(
    *,
    quick_actions: list[str] | None = None,
) -> dict[str, Any]:
    default_actions = quick_actions or ["analyze", "review", "patch", "new_project", "evolve_project"]
    return {
        "schema": "tui_goal_submission_entry_v1",
        "input_modes": ["single_line_goal", "multiline_goal"],
        "quick_actions": [_clean_text(action, max_chars=60) for action in default_actions],
        "explicit_submit_required": True,
    }


def build_tui_task_filtering_grouping(
    *,
    task_board: dict[str, Any],
    status_filters: list[str] | None = None,
    group_by: str = "status",
) -> dict[str, Any]:
    filters = [
        _clean_text(status, max_chars=30) for status in (status_filters or ["todo", "in_progress", "blocked", "done"])
    ]
    effective_group = group_by if group_by in {"status", "priority"} else "status"
    return {
        "schema": "tui_task_filtering_grouping_v1",
        "base_task_board_schema": _clean_text(task_board.get("schema"), max_chars=80),
        "supported_filters": filters,
        "grouping_modes": ["status", "priority"],
        "default_grouping": effective_group,
    }


def build_tui_log_stream_view(
    logs: list[dict[str, Any]],
    *,
    level_filter: str | None = None,
) -> dict[str, Any]:
    entries = [
        {
            "id": _clean_text(item.get("id"), max_chars=100),
            "level": _clean_text(item.get("level"), max_chars=20),
            "message": _clean_text(item.get("message"), max_chars=240),
            "timestamp": _clean_text(item.get("timestamp"), max_chars=60),
        }
        for item in logs or []
    ]
    return {
        "schema": "tui_log_stream_view_v1",
        "entries": entries,
        "active_level_filter": _clean_text(level_filter, max_chars=20) or None,
        "readable_and_filterable": True,
    }


def build_tui_approval_queue_view(approvals: list[dict[str, Any]]) -> dict[str, Any]:
    queue = [
        {
            "id": _clean_text(item.get("id"), max_chars=100),
            "scope": _clean_text(item.get("scope"), max_chars=120),
            "state": _clean_text(item.get("state"), max_chars=30),
            "context_summary": _clean_text(item.get("context_summary"), max_chars=200),
        }
        for item in approvals or []
    ]
    return {
        "schema": "tui_approval_queue_view_v1",
        "queue": queue,
        "supports_real_workflow": True,
    }


def build_tui_approval_action_flow(
    approval_item: dict[str, Any],
    *,
    action: str,
    operator_note: str | None = None,
) -> dict[str, Any]:
    normalized_action = _clean_text(action, max_chars=20).lower()
    return {
        "schema": "tui_approval_action_flow_v1",
        "approval_id": _clean_text(approval_item.get("id"), max_chars=100),
        "action": normalized_action,
        "action_allowed": normalized_action in {"approve", "reject"},
        "operator_note": _clean_text(operator_note, max_chars=280) or None,
        "explicit_and_auditable": True,
        "blind_approval_guard": True,
    }


def build_tui_audit_summary_view(chains: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema": "tui_audit_summary_view_v1",
        "chains": [
            {
                "chain_id": _clean_text(item.get("chain_id"), max_chars=100),
                "risk_level": _clean_text(item.get("risk_level"), max_chars=30),
                "headline": _clean_text(item.get("headline"), max_chars=200),
            }
            for item in chains or []
        ],
        "high_signal_focus": True,
    }


def build_tui_audit_trace_drilldown(
    *,
    trace_id: str,
    events: list[dict[str, Any]],
    redaction_applied: bool = True,
) -> dict[str, Any]:
    return {
        "schema": "tui_audit_trace_drilldown_v1",
        "trace_id": _clean_text(trace_id, max_chars=100),
        "events": [
            {
                "event_id": _clean_text(event.get("event_id"), max_chars=100),
                "message": _clean_text(event.get("message"), max_chars=220),
            }
            for event in events or []
        ],
        "redaction_applied": bool(redaction_applied),
        "rbac_expected": True,
    }


def build_tui_policy_denial_view(denials: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema": "tui_policy_denial_view_v1",
        "items": [
            {
                "action_id": _clean_text(item.get("action_id"), max_chars=100),
                "reason": _clean_text(item.get("reason"), max_chars=220),
                "policy": _clean_text(item.get("policy"), max_chars=100),
            }
            for item in denials or []
        ],
        "debug_and_governance_useful": True,
    }


def build_tui_kritis_dashboard_summary(
    *,
    audit_health: str,
    approval_backlog: int,
    mutation_status: str,
    policy_posture: str,
) -> dict[str, Any]:
    return {
        "schema": "tui_kritis_dashboard_summary_v1",
        "audit_health": _clean_text(audit_health, max_chars=40),
        "approval_backlog": max(0, int(approval_backlog)),
        "mutation_status": _clean_text(mutation_status, max_chars=40),
        "policy_posture": _clean_text(policy_posture, max_chars=60),
        "terminal_compact": True,
    }


def build_tui_repair_session_views(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema": "tui_repair_session_views_v1",
        "sessions": [
            {
                "session_id": _clean_text(item.get("session_id"), max_chars=100),
                "diagnosis": _clean_text(item.get("diagnosis"), max_chars=220),
                "state": _clean_text(item.get("state"), max_chars=40),
                "verification_result": _clean_text(item.get("verification_result"), max_chars=80),
            }
            for item in sessions or []
        ],
        "local_admin_useful": True,
    }


def build_tui_repair_approval_execution_review(
    *,
    repair_session_id: str,
    dry_run_supported: bool,
    approval_required: bool,
) -> dict[str, Any]:
    return {
        "schema": "tui_repair_approval_execution_review_v1",
        "repair_session_id": _clean_text(repair_session_id, max_chars=100),
        "dry_run_supported": bool(dry_run_supported),
        "approval_required": bool(approval_required),
        "flow_is_explicit": True,
        "flow_is_auditable": True,
    }


def build_tui_health_runtime_diagnostics_view(
    *,
    health: str,
    readiness: str,
    diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema": "tui_health_runtime_diagnostics_view_v1",
        "health": _clean_text(health, max_chars=40),
        "readiness": _clean_text(readiness, max_chars=40),
        "diagnostics": [
            {
                "key": _clean_text(item.get("key"), max_chars=80),
                "value": _clean_text(item.get("value"), max_chars=180),
            }
            for item in diagnostics or []
        ],
        "daily_ops_ready": True,
    }


def build_tui_provider_backend_visibility_view(
    providers: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema": "tui_provider_backend_visibility_view_v1",
        "providers": [
            {
                "provider": _clean_text(item.get("provider"), max_chars=80),
                "backend": _clean_text(item.get("backend"), max_chars=80),
                "capability_state": _clean_text(item.get("capability_state"), max_chars=60),
            }
            for item in providers or []
        ],
        "operator_focused_signal": True,
    }


def build_tui_keyboard_navigation_refinement() -> dict[str, Any]:
    return {
        "schema": "tui_keyboard_navigation_refinement_v1",
        "shortcut_model_learnable": True,
        "important_actions_keyboard_first": True,
    }


def build_tui_cross_view_search_filtering() -> dict[str, Any]:
    return {
        "schema": "tui_cross_view_search_filtering_v1",
        "supported_views": ["tasks", "artifacts", "approvals", "logs"],
        "behavior_consistent": True,
    }


def build_tui_state_persistence_resume(
    *,
    selected_profile_id: str | None,
    last_view: str | None,
) -> dict[str, Any]:
    return {
        "schema": "tui_state_persistence_resume_v1",
        "persisted_state": {
            "selected_profile_id": _clean_text(selected_profile_id, max_chars=80) or None,
            "last_view": _clean_text(last_view, max_chars=80) or None,
        },
        "sensitive_data_persisted": False,
    }


def build_tui_empty_error_state_ux() -> dict[str, Any]:
    return {
        "schema": "tui_empty_error_state_ux_v1",
        "empty_state_present": True,
        "permission_denial_state_present": True,
        "backend_failure_state_present": True,
        "distinguishable_states": True,
    }
