from __future__ import annotations

from typing import Any

from agent.services._eclipse_plugin_utils import (
    _clean_text,
    _compact_artifact,
    _compact_task,
    _normalize_profile,
    build_eclipse_diff_review_render,
)


def build_eclipse_view_strategy() -> dict[str, Any]:
    return {
        "schema": "eclipse_view_strategy_v1",
        "mvp_views": [
            "goal_quick_action_view",
            "task_list_view",
            "artifact_view",
            "context_inspection_view",
            "task_detail_view",
            "review_proposal_view",
            "blueprint_work_profile_view",
        ],
        "phase_two_views": [
            "extended_filters_and_grouping",
            "advanced_render_modes",
            "enhanced_view_synchronization",
        ],
        "browser_only_views": [
            "deep_admin_configuration",
            "deep_audit_chains",
            "provider_governance_detail",
        ],
        "maps_to_backend_flows": True,
        "does_not_replace_web_ui": True,
    }


def build_eclipse_goal_quick_action_view(
    *,
    goal_text: str,
    workspace_context: dict[str, Any],
    quick_actions: list[str] | None = None,
) -> dict[str, Any]:
    actions = quick_actions or ["analyze", "review", "patch", "new_project", "evolve_project"]
    return {
        "schema": "eclipse_goal_quick_action_view_v1",
        "goal_text": _clean_text(goal_text, max_chars=600),
        "quick_actions": [_clean_text(item, max_chars=80) for item in actions],
        "workspace_context": {
            "project_name": workspace_context.get("project_name"),
            "active_file_path": workspace_context.get("active_file_path"),
        },
        "bounded_context_before_submit": True,
    }


def build_eclipse_task_list_view(
    tasks: list[dict[str, Any]],
    *,
    max_items: int = 50,
) -> dict[str, Any]:
    item_limit = max(1, int(max_items))
    return {
        "schema": "eclipse_task_list_view_v1",
        "items": [_compact_task(item) for item in tasks[:item_limit]],
        "detail_view_link_available": True,
        "shows_status_review_next_step": True,
    }


def build_eclipse_artifact_view(
    artifacts: list[dict[str, Any]],
    *,
    max_items: int = 50,
) -> dict[str, Any]:
    item_limit = max(1, int(max_items))
    return {
        "schema": "eclipse_artifact_view_v1",
        "items": [_compact_artifact(item) for item in artifacts[:item_limit]],
        "bounded_rendering": True,
        "related_object_navigation": True,
        "open_in_browser_available": True,
    }


def build_eclipse_context_inspection_view(
    *,
    workspace_context: dict[str, Any],
    handoff_context: dict[str, Any],
) -> dict[str, Any]:
    selected_paths = list(workspace_context.get("selected_paths") or [])
    return {
        "schema": "eclipse_context_inspection_view_v1",
        "workspace_path": workspace_context.get("workspace_path"),
        "project_name": workspace_context.get("project_name"),
        "active_file_path": workspace_context.get("active_file_path"),
        "selected_paths_count": len(selected_paths),
        "handoff_scope": handoff_context.get("scope"),
        "can_remove_or_adjust_context": True,
        "bounded_visible_context": True,
    }


def build_eclipse_basic_task_detail_view(
    task: dict[str, Any],
    *,
    artifacts: list[dict[str, Any]] | None = None,
    routing_hints: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema": "eclipse_basic_task_detail_view_v1",
        "task": _compact_task(task),
        "summary": _clean_text(task.get("summary") or task.get("title"), max_chars=320),
        "routing_hints": [_clean_text(hint, max_chars=120) for hint in (routing_hints or [])],
        "artifacts": [_compact_artifact(item) for item in (artifacts or [])],
        "not_an_ops_dashboard": True,
    }


def build_eclipse_review_proposal_view(
    *,
    proposals: list[dict[str, Any]],
    approval_actions_supported: bool,
) -> dict[str, Any]:
    rendered = build_eclipse_diff_review_render(proposals)["proposals"]
    return {
        "schema": "eclipse_review_proposal_view_v1",
        "proposals": rendered,
        "explicit_approval_actions_supported": bool(approval_actions_supported),
        "auditable_review_workflow": True,
    }


def build_eclipse_blueprint_work_profile_view(
    blueprints: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema": "eclipse_blueprint_work_profile_view_v1",
        "blueprints": [
            {
                "id": _clean_text(item.get("id"), max_chars=80),
                "purpose": _clean_text(item.get("purpose"), max_chars=220),
                "recommended_goal_modes": [
                    _clean_text(mode, max_chars=80) for mode in list(item.get("recommended_goal_modes") or [])
                ],
                "typical_outputs": [
                    _clean_text(output, max_chars=120) for output in list(item.get("typical_outputs") or [])
                ],
            }
            for item in blueprints
        ],
        "supports_starting_path_selection": True,
    }


def build_eclipse_connection_runtime_status_view(
    *,
    profile: dict[str, Any],
    connected: bool,
    health_state: str,
    required_capabilities: list[str] | None = None,
) -> dict[str, Any]:
    normalized = _normalize_profile(profile)
    capabilities = [_clean_text(item, max_chars=80) for item in (required_capabilities or [])]
    return {
        "schema": "eclipse_connection_runtime_status_view_v1",
        "profile": normalized,
        "connected": bool(connected),
        "health_state": _clean_text(health_state, max_chars=40).lower(),
        "required_capabilities": capabilities,
        "lightweight_not_full_admin_surface": True,
    }


def build_eclipse_view_navigation_linking_model() -> dict[str, Any]:
    return {
        "schema": "eclipse_view_navigation_linking_model_v1",
        "links": [
            {"from": "goal_quick_action_view", "to": "task_list_view"},
            {"from": "task_list_view", "to": "task_detail_view"},
            {"from": "task_detail_view", "to": "artifact_view"},
            {"from": "review_proposal_view", "to": "task_detail_view"},
            {"from": "any_view", "to": "open_in_browser_shortcuts"},
        ],
        "preserves_task_context": True,
        "cross_link_to_web_ui": True,
    }


def build_eclipse_view_selection_synchronization(
    *,
    active_object: dict[str, Any],
    source_view: str,
    target_views: list[str],
) -> dict[str, Any]:
    active_id = _clean_text(active_object.get("id"), max_chars=100)
    active_type = _clean_text(active_object.get("type") or "task", max_chars=40).lower()
    return {
        "schema": "eclipse_view_selection_synchronization_v1",
        "source_view": _clean_text(source_view, max_chars=80),
        "target_views": [_clean_text(item, max_chars=80) for item in target_views if _clean_text(item, max_chars=80)],
        "active_object": {
            "id": active_id or None,
            "type": active_type or "task",
            "title": _clean_text(active_object.get("title"), max_chars=200) or None,
        },
        "active_object_visible": bool(active_id),
        "predictable_not_magical": True,
    }


def build_eclipse_minimal_view_state_persistence(
    persisted_candidate_state: dict[str, Any],
    *,
    allowed_keys: list[str] | None = None,
) -> dict[str, Any]:
    allowed = set(
        allowed_keys
        or [
            "connection_profile_id",
            "task_filter",
            "task_grouping",
            "artifact_render_mode",
            "last_selected_task_id",
            "last_selected_artifact_id",
            "last_opened_view",
        ]
    )
    persisted_state: dict[str, Any] = {}
    skipped_keys: list[str] = []
    for key, value in persisted_candidate_state.items():
        key_name = _clean_text(key, max_chars=80)
        key_lower = key_name.lower()
        if key_name in allowed and all(pattern not in key_lower for pattern in {"token", "secret", "password"}):
            persisted_state[key_name] = _clean_text(value, max_chars=200)
        else:
            skipped_keys.append(key_name)
    return {
        "schema": "eclipse_minimal_view_state_persistence_v1",
        "persisted_state": persisted_state,
        "persisted_keys": list(persisted_state.keys()),
        "skipped_keys": skipped_keys,
        "restored_across_restart": True,
        "sensitive_state_excluded": True,
    }


def build_eclipse_task_filters_grouping(
    tasks: list[dict[str, Any]],
    *,
    status_filter: list[str] | None = None,
    review_required_only: bool = False,
    group_by: str = "status",
) -> dict[str, Any]:
    allowed_statuses = {_clean_text(item, max_chars=30).lower() for item in (status_filter or [])}
    compact_tasks = [_compact_task(task) for task in tasks]
    filtered = [
        task
        for task in compact_tasks
        if (not allowed_statuses or task["status"].lower() in allowed_statuses)
        and (not review_required_only or task["review_required"])
    ]
    normalized_group_by = _clean_text(group_by, max_chars=30).lower()
    groups: dict[str, list[dict[str, Any]]] = {}
    for task in filtered:
        if normalized_group_by == "goal":
            key = task["goal_id"] or "no_goal"
        else:
            key = task["status"] or "unknown"
        groups.setdefault(key, []).append(task)
    return {
        "schema": "eclipse_task_filters_grouping_v1",
        "filtered_items": filtered,
        "groups": [
            {"group": group_name, "count": len(group_tasks), "task_ids": [item["id"] for item in group_tasks]}
            for group_name, group_tasks in groups.items()
        ],
        "active_filter": sorted(allowed_statuses),
        "review_required_only": bool(review_required_only),
        "group_by": "goal" if normalized_group_by == "goal" else "status",
        "lightweight_not_project_management_clone": True,
    }


def build_eclipse_artifact_render_modes(
    artifacts: list[dict[str, Any]],
) -> dict[str, Any]:
    rendered_items = []
    for artifact in artifacts:
        compact = _compact_artifact(artifact)
        artifact_type = compact["type"].lower()
        if artifact_type in {"markdown", "md", "text/markdown"}:
            mode = "markdown_text"
        elif artifact_type in {"review", "proposal", "diff", "patch"}:
            mode = "proposal_review"
        elif artifact_type in {"summary", "text"}:
            mode = "summary"
        else:
            mode = "raw_text"
        rendered_items.append(
            {
                **compact,
                "render_mode": mode,
                "fallback_to_browser": mode == "raw_text",
            }
        )
    return {
        "schema": "eclipse_artifact_render_modes_v1",
        "items": rendered_items,
        "supported_modes": ["summary", "markdown_text", "proposal_review", "raw_text"],
        "unsupported_degrades_to_raw_or_browser": True,
    }


def build_eclipse_context_source_badges(
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    label_map = {
        "selection": "Selection",
        "repo_context": "Repo context",
        "task_memory": "Task memory",
        "research": "Research",
    }
    badges = []
    for item in items:
        source = _clean_text(item.get("source") or "unknown", max_chars=40).lower()
        badges.append(
            {
                "object_id": _clean_text(item.get("id"), max_chars=100) or None,
                "source": source,
                "badge": label_map.get(source, "Other source"),
            }
        )
    return {
        "schema": "eclipse_context_source_badges_v1",
        "badges": badges,
        "lightweight_and_readable": True,
        "improves_source_trust": True,
    }


def build_eclipse_first_run_perspective_layout() -> dict[str, Any]:
    return {
        "schema": "eclipse_first_run_perspective_layout_v1",
        "recommended_views": [
            "goal_quick_action_view",
            "task_list_view",
            "artifact_view",
            "context_inspection_view",
        ],
        "optional_secondary_views": [
            "task_detail_view",
            "review_proposal_view",
        ],
        "sensible_layout_without_manual_assembly": True,
        "documented_or_shipped": True,
    }


def build_eclipse_browser_fallback_policy() -> dict[str, Any]:
    return {
        "schema": "eclipse_browser_fallback_policy_v1",
        "rules": [
            {"surface": "goal_task_artifact_context_views", "decision": "render_in_eclipse"},
            {"surface": "deep_admin_configuration", "decision": "open_in_browser"},
            {"surface": "deep_audit_drilldown", "decision": "open_in_browser"},
            {"surface": "provider_governance_detail", "decision": "open_in_browser"},
        ],
        "complex_admin_audit_remains_browser_first": True,
        "prevents_second_full_frontend": True,
    }


def build_eclipse_view_error_empty_state_catalog(
    views: list[str] | None = None,
) -> dict[str, Any]:
    view_names = views or [
        "goal_quick_action_view",
        "task_list_view",
        "artifact_view",
        "context_inspection_view",
        "task_detail_view",
        "review_proposal_view",
    ]
    state_templates = {
        "no_data": {
            "title": "No data yet",
            "hint": "Run an action from Goal view to populate this view.",
        },
        "disconnected": {
            "title": "Backend disconnected",
            "hint": "Check endpoint profile and retry connection.",
        },
        "permission_denied": {
            "title": "Permission required",
            "hint": "Open browser settings or request access under policy.",
        },
    }
    return {
        "schema": "eclipse_view_error_empty_state_catalog_v1",
        "views": {_clean_text(view, max_chars=80): state_templates for view in view_names},
        "distinguishes_no_data_disconnected_permission": True,
        "core_views_covered": True,
    }


def build_eclipse_accessibility_keyboard_support() -> dict[str, Any]:
    return {
        "schema": "eclipse_accessibility_keyboard_support_v1",
        "keyboard_actions": [
            {"action": "move_between_task_list_items", "keys": ["up", "down"]},
            {"action": "open_selected_task_detail", "keys": ["enter"]},
            {"action": "switch_focus_list_to_detail", "keys": ["tab", "shift+tab"]},
            {"action": "open_browser_shortcut_for_active_object", "keys": ["ctrl+enter"]},
        ],
        "core_flows_without_mouse_dependency": True,
        "usable_for_daily_development_flow": True,
    }


def build_eclipse_multi_view_smoke_checklist() -> dict[str, Any]:
    return {
        "schema": "eclipse_multi_view_smoke_checklist_v1",
        "checks": [
            "create_goal_from_goal_view",
            "verify_task_appears_in_task_list",
            "verify_context_view_matches_selected_task",
            "verify_artifact_view_shows_related_output",
            "verify_detail_and_review_links_preserve_active_task",
            "verify_selection_switch_updates_linked_views_without_stale_state",
        ],
        "release_smoke_ready": True,
        "focuses_on_multi_view_linking_and_state_freshness": True,
    }


def build_eclipse_view_model_coordination_test_matrix() -> dict[str, Any]:
    return {
        "schema": "eclipse_view_model_coordination_test_matrix_v1",
        "coverage_targets": [
            "selection_synchronization_between_views",
            "task_filter_and_grouping",
            "artifact_render_mode_fallbacks",
            "error_empty_state_catalog",
        ],
        "protects_task_artifact_detail_sync": True,
        "supports_regression_prevention": True,
    }


def build_eclipse_knowledge_sources_view_evaluation() -> dict[str, Any]:
    return {
        "schema": "eclipse_knowledge_sources_view_evaluation_v1",
        "candidate_sources": ["repo_context", "task_memory", "wiki_research_future"],
        "decision": "defer_to_later_phase",
        "mvp_blocked": False,
        "reason": "Keep first useful release compact and avoid overloading Eclipse with non-core visualizations.",
    }


def build_eclipse_advanced_admin_views_isolation() -> dict[str, Any]:
    return {
        "schema": "eclipse_advanced_admin_views_isolation_v1",
        "admin_view_candidates": [
            "provider_health_detail",
            "governance_decision_traces",
            "deep_audit_inspection",
        ],
        "separate_backlog_required": True,
        "excluded_from_main_plugin_roadmap": True,
        "work_views_vs_admin_views_boundary_clear": True,
    }
