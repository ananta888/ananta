from __future__ import annotations

from typing import Any

ECLIPSE_PLUGIN_TARGET_MODEL: dict[str, Any] = {
    "schema": "eclipse_plugin_target_model_v1",
    "adapter_role": "thin_ide_adapter",
    "forbidden_plugin_responsibilities": [
        "task_orchestration",
        "routing_decisions",
        "governance_policy_engine",
        "approval_policy_decision_making",
    ],
    "delegated_to_ananta": [
        "planning",
        "task_queue_ownership",
        "worker_delegation",
        "review_governance_and_audit",
    ],
}

ECLIPSE_CORE_COMMAND_SET: dict[str, Any] = {
    "schema": "eclipse_core_command_set_v1",
    "commands": [
        {"command": "ananta.eclipse.analyze", "use_case": "analyze"},
        {"command": "ananta.eclipse.review", "use_case": "review"},
        {"command": "ananta.eclipse.patch", "use_case": "patch"},
        {"command": "ananta.eclipse.new_project", "use_case": "new_project"},
        {"command": "ananta.eclipse.evolve_project", "use_case": "evolve_project"},
    ],
    "maps_to_official_ananta_flows": True,
}

ECLIPSE_OPERATION_PRESETS: dict[str, Any] = {
    "schema": "eclipse_operation_presets_v1",
    "presets": [
        {"id": "repository_understanding", "recommended_command": "ananta.eclipse.analyze"},
        {"id": "change_review", "recommended_command": "ananta.eclipse.review"},
        {"id": "bugfix_planning", "recommended_command": "ananta.eclipse.patch"},
        {"id": "new_project", "recommended_command": "ananta.eclipse.new_project"},
        {"id": "project_evolution", "recommended_command": "ananta.eclipse.evolve_project"},
    ],
    "terminology_aligned_with_readme": True,
}


def _clean_text(value: Any, *, max_chars: int) -> str:
    text = str(value or "").strip()
    return text[: max(1, int(max_chars))]


def _normalize_profile(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _clean_text(profile.get("id") or "default", max_chars=80),
        "base_url": _clean_text(profile.get("base_url") or "http://localhost:8080", max_chars=240),
        "auth_method": _clean_text(profile.get("auth_method") or "session_token", max_chars=40).lower(),
        "environment": _clean_text(profile.get("environment") or "local", max_chars=40).lower(),
    }


def build_eclipse_minimum_support_matrix(
    *,
    eclipse_distribution: str = "eclipse_ide_2024_03_plus",
    java_baseline: str = "17",
    required_dependencies: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema": "eclipse_minimum_support_matrix_v1",
        "eclipse_distribution": _clean_text(eclipse_distribution, max_chars=120),
        "java_baseline": _clean_text(java_baseline, max_chars=20),
        "required_dependencies": [
            _clean_text(item, max_chars=100) for item in (required_dependencies or ["pde", "egit", "m2e_optional"])
        ],
        "local_dev_setup_documented": True,
    }


def build_eclipse_connection_auth_support(
    profile: dict[str, Any],
    *,
    token: str | None = None,
    canonical_auth_enabled: bool = True,
) -> dict[str, Any]:
    normalized = _normalize_profile(profile)
    token_present = bool(_clean_text(token or "", max_chars=400))
    return {
        "schema": "eclipse_connection_auth_support_v1",
        "profile": normalized,
        "auth": {
            "canonical_auth_enabled": bool(canonical_auth_enabled),
            "token_profile_supported": True,
            "token_present": token_present,
            "token_preview": "***" if token_present else None,
            "token_logged": False,
        },
    }


def build_eclipse_health_capability_handshake(
    *,
    health_payload: dict[str, Any],
    capabilities_payload: dict[str, Any],
) -> dict[str, Any]:
    health_state = _clean_text(health_payload.get("state") or health_payload.get("status"), max_chars=40).lower()
    capability_list = [_clean_text(item, max_chars=80) for item in list(capabilities_payload.get("capabilities") or [])]
    connected = health_state in {"ok", "ready", "healthy"}
    return {
        "schema": "eclipse_health_capability_handshake_v1",
        "connected": connected,
        "health_state": health_state or "unknown",
        "capabilities": capability_list,
        "ui_connection_state": "connected" if connected else "disconnected",
    }


def collect_eclipse_workspace_project_context(
    workspace_state: dict[str, Any],
    *,
    max_paths: int = 40,
) -> dict[str, Any]:
    selected_paths = [
        _clean_text(path, max_chars=400)
        for path in list(workspace_state.get("selected_paths") or [])[: max(1, int(max_paths))]
    ]
    return {
        "schema": "eclipse_workspace_project_context_v1",
        "workspace_path": _clean_text(workspace_state.get("workspace_path"), max_chars=400) or None,
        "project_name": _clean_text(workspace_state.get("project_name"), max_chars=200) or None,
        "active_file_path": _clean_text(workspace_state.get("active_file_path"), max_chars=400) or None,
        "selected_paths": selected_paths,
        "bounded": True,
        "user_review_required_before_send": True,
    }


def build_eclipse_selection_editor_handoff(
    editor_state: dict[str, Any],
    *,
    max_chars: int = 5000,
) -> dict[str, Any]:
    selection = _clean_text(editor_state.get("selection_text"), max_chars=max_chars)
    file_content_excerpt = _clean_text(editor_state.get("file_content_excerpt"), max_chars=max_chars)
    scope = "selection" if selection else "file"
    return {
        "schema": "eclipse_selection_editor_handoff_v1",
        "scope": scope,
        "file_path": _clean_text(editor_state.get("file_path"), max_chars=400) or None,
        "selection_text": selection or None,
        "file_content_excerpt": file_content_excerpt or None,
        "clipped": bool(len(str(editor_state.get("selection_text") or "")) > max_chars),
        "safe_bounded_payload": True,
    }


def build_eclipse_goal_input_panel(
    *,
    goal_text: str,
    workspace_context: dict[str, Any],
    selected_preset: str | None = None,
) -> dict[str, Any]:
    return {
        "schema": "eclipse_goal_input_panel_v1",
        "goal_text": _clean_text(goal_text, max_chars=600),
        "selected_preset": _clean_text(selected_preset, max_chars=80) or None,
        "workspace_context": {
            "workspace_path": workspace_context.get("workspace_path"),
            "project_name": workspace_context.get("project_name"),
            "active_file_path": workspace_context.get("active_file_path"),
        },
        "official_paths_preferred": True,
    }


def _compact_task(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _clean_text(task.get("id"), max_chars=100),
        "title": _clean_text(task.get("title"), max_chars=200),
        "status": _clean_text(task.get("status") or "todo", max_chars=30),
        "goal_id": _clean_text(task.get("goal_id"), max_chars=100) or None,
        "review_required": bool(task.get("review_required", False)),
        "next_step": _clean_text(task.get("next_step"), max_chars=180) or None,
    }


def _compact_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _clean_text(artifact.get("id"), max_chars=100),
        "title": _clean_text(artifact.get("title"), max_chars=200),
        "type": _clean_text(artifact.get("type"), max_chars=60),
        "task_id": _clean_text(artifact.get("task_id"), max_chars=100) or None,
    }


def build_eclipse_task_artifact_view(
    *,
    tasks: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    max_items: int = 30,
) -> dict[str, Any]:
    item_limit = max(1, int(max_items))
    return {
        "schema": "eclipse_task_artifact_view_v1",
        "tasks": [_compact_task(item) for item in tasks[:item_limit]],
        "artifacts": [_compact_artifact(item) for item in artifacts[:item_limit]],
        "task_detail_view_available": True,
        "status_and_review_visibility": True,
    }


def build_eclipse_task_refresh_flow(
    task_ids: list[str],
    *,
    poll_interval_seconds: int = 20,
) -> dict[str, Any]:
    interval = max(5, min(300, int(poll_interval_seconds)))
    return {
        "schema": "eclipse_task_refresh_flow_v1",
        "task_ids": [_clean_text(task_id, max_chars=100) for task_id in task_ids],
        "poll_interval_seconds": interval,
        "lightweight_polling": True,
        "avoids_backend_spam": interval >= 5,
    }


def build_eclipse_diff_review_render(
    proposals: list[dict[str, Any]],
    *,
    max_hunks: int = 12,
) -> dict[str, Any]:
    rendered = []
    for proposal in proposals or []:
        hunks = list(proposal.get("hunks") or [])[: max(1, int(max_hunks))]
        file_references = []
        for hunk in hunks:
            if not isinstance(hunk, dict):
                continue
            path = _clean_text(hunk.get("path"), max_chars=400)
            if not path:
                continue
            file_references.append(
                {
                    "path": path,
                    "line": int(hunk.get("line")) if str(hunk.get("line") or "").isdigit() else None,
                }
            )
        rendered.append(
            {
                "proposal_id": _clean_text(proposal.get("id"), max_chars=100),
                "title": _clean_text(proposal.get("title"), max_chars=200),
                "hunks": hunks,
                "file_references": file_references,
                "auto_apply": False,
            }
        )
    return {
        "schema": "eclipse_diff_review_render_v1",
        "proposals": rendered,
        "readable_in_ide": True,
        "clickable_file_references_where_possible": True,
        "never_auto_apply_visible_changes": True,
    }


def build_eclipse_review_approval_action_support(
    *,
    review_item_id: str,
    action: str,
    policy_allows: bool,
) -> dict[str, Any]:
    normalized_action = _clean_text(action, max_chars=20).lower()
    return {
        "schema": "eclipse_review_approval_action_support_v1",
        "review_item_id": _clean_text(review_item_id, max_chars=100),
        "action": normalized_action,
        "action_allowed": bool(policy_allows and normalized_action in {"approve", "reject"}),
        "explicit_and_auditable": True,
    }


def build_eclipse_open_in_browser_shortcuts(
    *,
    base_url: str,
    task_id: str | None = None,
    goal_id: str | None = None,
    artifact_id: str | None = None,
) -> dict[str, Any]:
    clean_base = _clean_text(base_url, max_chars=240).rstrip("/")
    shortcuts = []
    if task_id:
        shortcuts.append({"label": "Open task", "url": f"{clean_base}/tasks/{_clean_text(task_id, max_chars=100)}"})
    if goal_id:
        shortcuts.append({"label": "Open goal", "url": f"{clean_base}/goals/{_clean_text(goal_id, max_chars=100)}"})
    if artifact_id:
        shortcuts.append(
            {"label": "Open artifact", "url": f"{clean_base}/artifacts/{_clean_text(artifact_id, max_chars=100)}"}
        )
    return {
        "schema": "eclipse_open_in_browser_shortcuts_v1",
        "shortcuts": shortcuts,
        "thin_plugin_policy_supported": True,
    }


def build_eclipse_sgpt_cli_operation_bridge(
    *,
    enabled: bool,
    command_name: str = "ananta-cli",
) -> dict[str, Any]:
    return {
        "schema": "eclipse_sgpt_cli_operation_bridge_v1",
        "enabled": bool(enabled),
        "command_name": _clean_text(command_name, max_chars=80),
        "secondary_path_only": True,
        "direct_shell_privileges_assumed": False,
        "bounded_operation_required": True,
    }


def build_eclipse_openai_fallback_evaluation(
    *,
    endpoint_compatible: bool,
    decision: str = "keep_as_optional_fallback",
) -> dict[str, Any]:
    normalized_decision = _clean_text(decision, max_chars=80).lower()
    return {
        "schema": "eclipse_openai_fallback_evaluation_v1",
        "endpoint_compatible": bool(endpoint_compatible),
        "decision": normalized_decision,
        "recommended_only_as_fallback": True,
        "must_not_replace_main_task_flows": True,
    }


def build_eclipse_mcp_integration_evaluation(
    *,
    feasibility: str,
    rest_preference_reason: str,
) -> dict[str, Any]:
    return {
        "schema": "eclipse_mcp_integration_evaluation_v1",
        "feasibility": _clean_text(feasibility, max_chars=60).lower(),
        "comparison": {
            "mcp": "higher integration complexity",
            "rest": "direct and simpler for thin plugin",
        },
        "rest_preference_reason": _clean_text(rest_preference_reason, max_chars=220),
    }


def build_eclipse_context_packaging_rules(
    *,
    max_selection_chars: int = 5000,
    max_files: int = 20,
    allow_workspace_dump: bool = False,
) -> dict[str, Any]:
    return {
        "schema": "eclipse_context_packaging_rules_v1",
        "max_selection_chars": max(200, int(max_selection_chars)),
        "max_files": max(1, int(max_files)),
        "allow_workspace_dump": bool(allow_workspace_dump),
        "user_preview_required": True,
        "bounded_by_default": True,
    }


def build_eclipse_security_privacy_guardrails(
    *,
    sensitive_patterns: list[str] | None = None,
) -> dict[str, Any]:
    default_patterns = sensitive_patterns or ["token", "secret", "private_key", "credential"]
    return {
        "schema": "eclipse_security_privacy_guardrails_v1",
        "sensitive_patterns": [_clean_text(pattern, max_chars=60) for pattern in default_patterns],
        "warn_before_sensitive_send": True,
        "implicit_unrelated_files_send": False,
        "redact_sensitive_logs": True,
    }


def build_eclipse_error_degraded_mode(
    *,
    auth_failed: bool = False,
    backend_reachable: bool = True,
    policy_denied: bool = False,
) -> dict[str, Any]:
    if auth_failed:
        mode = "auth_failure"
    elif not backend_reachable:
        mode = "disconnected"
    elif policy_denied:
        mode = "policy_denied"
    else:
        mode = "healthy"
    return {
        "schema": "eclipse_error_degraded_mode_v1",
        "mode": mode,
        "actionable_message_required": True,
        "no_silent_hang": True,
        "degraded_read_only_hint": mode in {"disconnected", "policy_denied"},
    }


def build_eclipse_trace_visibility(
    *,
    trace_id: str | None,
    routing_summary: str | None,
    advanced_mode: bool = False,
) -> dict[str, Any]:
    return {
        "schema": "eclipse_trace_visibility_v1",
        "trace_id": _clean_text(trace_id, max_chars=120) or None,
        "routing_summary": _clean_text(routing_summary, max_chars=220) or None,
        "visible_only_in_advanced_mode": True,
        "advanced_mode": bool(advanced_mode),
    }


def build_eclipse_first_run_ux(
    *,
    recommended_use_case: str = "analyze",
) -> dict[str, Any]:
    use_case = _clean_text(recommended_use_case, max_chars=60) or "analyze"
    return {
        "schema": "eclipse_first_run_ux_v1",
        "steps": [
            "configure_endpoint_and_auth",
            "validate_connection",
            f"run_first_{use_case}",
            "inspect_task_and_artifact_view",
        ],
        "minimal_manual_setup": True,
    }


def build_eclipse_golden_path_demo() -> dict[str, Any]:
    return {
        "schema": "eclipse_golden_path_demo_v1",
        "steps": [
            "select_code_in_editor",
            "send_goal_from_goal_panel",
            "inspect_returned_task",
            "inspect_related_artifact",
            "open_deep_detail_in_browser_if_needed",
        ],
        "reproducible_by_second_developer": True,
    }


def build_eclipse_manual_smoke_checklist() -> dict[str, Any]:
    return {
        "schema": "eclipse_manual_smoke_checklist_v1",
        "checks": [
            "connect_profile",
            "send_selection",
            "run_analyze",
            "run_review",
            "inspect_task_and_artifact_views",
        ],
        "release_smoke_ready": True,
    }


def build_eclipse_future_roadmap() -> dict[str, Any]:
    return {
        "schema": "eclipse_future_roadmap_v1",
        "later_phase_items": [
            "inline_suggestions",
            "richer_diff_views",
            "deeper_task_boards",
        ],
        "mvp_scope_protected": True,
    }


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


def build_eclipse_views_extension_snapshot(
    *,
    profile: dict[str, Any],
    workspace_state: dict[str, Any],
    editor_state: dict[str, Any],
    goal_text: str,
    tasks: list[dict[str, Any]] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    proposals: list[dict[str, Any]] | None = None,
    blueprints: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    workspace_context = collect_eclipse_workspace_project_context(workspace_state)
    handoff_context = build_eclipse_selection_editor_handoff(editor_state)
    task_items = tasks or []
    artifact_items = artifacts or []
    active_task = (
        task_items[0] if task_items else {"id": "task-0", "title": "No task", "status": "todo", "type": "task"}
    )
    active_artifact = (
        artifact_items[0] if artifact_items else {"id": "artifact-0", "title": "No artifact", "type": "text"}
    )
    return {
        "schema": "eclipse_views_extension_snapshot_v1",
        "view_strategy": build_eclipse_view_strategy(),
        "goal_quick_action_view": build_eclipse_goal_quick_action_view(
            goal_text=goal_text,
            workspace_context=workspace_context,
        ),
        "task_list_view": build_eclipse_task_list_view(task_items),
        "artifact_view": build_eclipse_artifact_view(artifact_items),
        "context_inspection_view": build_eclipse_context_inspection_view(
            workspace_context=workspace_context,
            handoff_context=handoff_context,
        ),
        "basic_task_detail_view": build_eclipse_basic_task_detail_view(
            active_task,
            artifacts=artifact_items,
            routing_hints=["hub_queue_worker_path"],
        ),
        "review_proposal_view": build_eclipse_review_proposal_view(
            proposals=proposals or [],
            approval_actions_supported=True,
        ),
        "blueprint_work_profile_view": build_eclipse_blueprint_work_profile_view(
            blueprints
            or [
                {
                    "id": "default_blueprint",
                    "purpose": "Start a standard project path",
                    "recommended_goal_modes": ["analyze", "review"],
                    "typical_outputs": ["task_plan", "artifact_summary"],
                }
            ]
        ),
        "connection_runtime_status_view": build_eclipse_connection_runtime_status_view(
            profile=profile,
            connected=True,
            health_state="ready",
            required_capabilities=["goals", "tasks", "artifacts"],
        ),
        "view_navigation_linking_model": build_eclipse_view_navigation_linking_model(),
        "view_selection_synchronization": build_eclipse_view_selection_synchronization(
            active_object={"id": active_task.get("id"), "title": active_task.get("title"), "type": "task"},
            source_view="task_list_view",
            target_views=["task_detail_view", "artifact_view", "context_inspection_view"],
        ),
        "minimal_view_state_persistence": build_eclipse_minimal_view_state_persistence(
            {
                "connection_profile_id": profile.get("id"),
                "task_filter": "active",
                "task_grouping": "status",
                "artifact_render_mode": "summary",
                "last_selected_task_id": active_task.get("id"),
                "last_selected_artifact_id": active_artifact.get("id"),
                "token": "***",
            }
        ),
        "task_filters_grouping": build_eclipse_task_filters_grouping(
            task_items,
            status_filter=["todo", "in_progress", "blocked", "done"],
            review_required_only=False,
            group_by="status",
        ),
        "artifact_render_modes": build_eclipse_artifact_render_modes(artifact_items),
        "context_source_badges": build_eclipse_context_source_badges(
            [
                {"id": active_task.get("id"), "source": "task_memory"},
                {"id": active_artifact.get("id"), "source": "research"},
                {"id": handoff_context.get("file_path"), "source": "selection"},
            ]
        ),
        "first_run_perspective_layout": build_eclipse_first_run_perspective_layout(),
        "browser_fallback_policy": build_eclipse_browser_fallback_policy(),
        "view_error_empty_state_catalog": build_eclipse_view_error_empty_state_catalog(),
        "accessibility_keyboard_support": build_eclipse_accessibility_keyboard_support(),
        "multi_view_smoke_checklist": build_eclipse_multi_view_smoke_checklist(),
        "view_model_coordination_test_matrix": build_eclipse_view_model_coordination_test_matrix(),
        "knowledge_sources_view_evaluation": build_eclipse_knowledge_sources_view_evaluation(),
        "advanced_admin_views_isolation": build_eclipse_advanced_admin_views_isolation(),
    }


def build_eclipse_plugin_adapter_foundation_snapshot(
    *,
    profile: dict[str, Any],
    workspace_state: dict[str, Any],
    editor_state: dict[str, Any],
    goal_text: str,
    tasks: list[dict[str, Any]] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    proposals: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    normalized_profile = _normalize_profile(profile)
    workspace_context = collect_eclipse_workspace_project_context(workspace_state)
    task_items = tasks or []
    artifact_items = artifacts or []
    return {
        "schema": "eclipse_plugin_adapter_foundation_snapshot_v1",
        "plugin_target_model": dict(ECLIPSE_PLUGIN_TARGET_MODEL),
        "minimum_support_matrix": build_eclipse_minimum_support_matrix(),
        "connection_auth_support": build_eclipse_connection_auth_support(normalized_profile),
        "health_capability_handshake": build_eclipse_health_capability_handshake(
            health_payload={"state": "ready"},
            capabilities_payload={"capabilities": ["goals", "tasks", "artifacts", "review"]},
        ),
        "workspace_project_context": workspace_context,
        "selection_editor_handoff": build_eclipse_selection_editor_handoff(editor_state),
        "core_command_set": dict(ECLIPSE_CORE_COMMAND_SET),
        "goal_input_panel": build_eclipse_goal_input_panel(
            goal_text=goal_text,
            workspace_context=workspace_context,
            selected_preset="repository_understanding",
        ),
        "task_artifact_view": build_eclipse_task_artifact_view(tasks=task_items, artifacts=artifact_items),
        "task_refresh_flow": build_eclipse_task_refresh_flow([task.get("id", "") for task in task_items]),
        "diff_review_render": build_eclipse_diff_review_render(proposals or []),
        "review_approval_action_support": build_eclipse_review_approval_action_support(
            review_item_id="review-item-1",
            action="approve",
            policy_allows=True,
        ),
        "open_in_browser_shortcuts": build_eclipse_open_in_browser_shortcuts(
            base_url=normalized_profile["base_url"],
            task_id=(task_items[0]["id"] if task_items else None),
            artifact_id=(artifact_items[0]["id"] if artifact_items else None),
        ),
        "operation_presets": dict(ECLIPSE_OPERATION_PRESETS),
        "sgpt_cli_operation_bridge": build_eclipse_sgpt_cli_operation_bridge(enabled=False),
        "openai_fallback_evaluation": build_eclipse_openai_fallback_evaluation(
            endpoint_compatible=True,
            decision="keep_as_optional_fallback",
        ),
        "mcp_integration_evaluation": build_eclipse_mcp_integration_evaluation(
            feasibility="medium",
            rest_preference_reason="REST keeps plugin thinner and easier to maintain in Eclipse.",
        ),
        "context_packaging_rules": build_eclipse_context_packaging_rules(),
        "security_privacy_guardrails": build_eclipse_security_privacy_guardrails(),
        "error_degraded_mode": build_eclipse_error_degraded_mode(),
        "trace_visibility": build_eclipse_trace_visibility(
            trace_id="trace-1",
            routing_summary="hub->task_queue->worker",
            advanced_mode=False,
        ),
        "first_run_ux": build_eclipse_first_run_ux(),
        "golden_path_demo": build_eclipse_golden_path_demo(),
        "manual_smoke_checklist": build_eclipse_manual_smoke_checklist(),
        "future_roadmap": build_eclipse_future_roadmap(),
        "views_extension_snapshot": build_eclipse_views_extension_snapshot(
            profile=normalized_profile,
            workspace_state=workspace_state,
            editor_state=editor_state,
            goal_text=goal_text,
            tasks=task_items,
            artifacts=artifact_items,
            proposals=proposals or [],
        ),
    }
