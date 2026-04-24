from __future__ import annotations

from typing import Any


SHARED_UI_BOUNDARY_MODEL: dict[str, Any] = {
    "schema": "editor_tui_shared_boundary_v1",
    "surfaces": {
        "nvim_plugin": {
            "focus": "developer_coding_review_context_goal_submission",
            "must_not_duplicate": ["orchestration", "routing", "governance", "approval_decisions"],
        },
        "tui": {
            "focus": "admin_task_artifact_approval_audit_operations",
            "must_not_duplicate": ["orchestration", "routing", "policy_enforcement"],
        },
        "browser": {
            "focus": "deep_configuration_and_full_detail_views",
            "fallback_when_terminal_surface_insufficient": True,
        },
    },
    "split_rules": {
        "developer_facing": ["nvim_plugin"],
        "operator_facing": ["tui"],
        "cross_surface_contract_source": "ananta_backend_contracts",
    },
}

FRONTEND_CONTRACT_INVENTORY: dict[str, Any] = {
    "schema": "editor_tui_contract_inventory_v1",
    "required_endpoint_domains": [
        "auth",
        "goals",
        "tasks",
        "artifacts",
        "approvals",
        "logs",
        "health",
        "runtime_diagnostics",
        "kritis_visibility",
    ],
    "notes": {
        "inventory_goal": "identify_missing_read_models_before_ui_implementation",
        "common_contracts_required": True,
    },
}

COMMON_OBJECT_MODEL: dict[str, Any] = {
    "schema": "editor_tui_common_object_model_v1",
    "objects": {
        "task_item": {
            "required_fields": ["id", "title", "status", "priority", "updated_at", "trace_id"],
            "compact_fields": ["id", "title", "status", "priority"],
        },
        "artifact_item": {
            "required_fields": ["id", "type", "title", "created_at", "related_task_id", "trace_id"],
            "compact_fields": ["id", "type", "title"],
        },
        "approval_item": {
            "required_fields": ["id", "state", "scope", "requested_by", "requested_at", "trace_id"],
            "compact_fields": ["id", "state", "scope"],
        },
        "log_entry": {
            "required_fields": ["id", "level", "message", "timestamp", "trace_id"],
            "compact_fields": ["level", "message", "timestamp"],
        },
        "trace_link": {
            "required_fields": ["trace_id", "task_id", "artifact_ids", "approval_ids", "log_ids"],
            "compact_fields": ["trace_id", "task_id"],
        },
    },
}

CAPABILITY_SURFACE_MODEL: dict[str, Any] = {
    "schema": "editor_tui_capability_surface_v1",
    "capability_categories": {
        "read_ops": ["task_read", "artifact_read", "log_read", "goal_read"],
        "write_ops": ["goal_submit", "task_update", "artifact_create"],
        "sensitive_ops": ["approval_decide", "repair_step_execute", "terminal_usage", "admin_config_change"],
    },
    "permission_states": ["allowed", "review_required", "blocked", "missing_capability"],
}

ERROR_DEGRADED_STATE_MODEL: dict[str, Any] = {
    "schema": "editor_tui_error_degraded_model_v1",
    "error_classes": [
        "auth_failure",
        "backend_unreachable",
        "missing_capability",
        "policy_denial",
        "runtime_failure",
    ],
    "degraded_states": [
        "read_only_mode",
        "offline_cache_only",
        "partial_data_mode",
    ],
    "silent_fallback_allowed": False,
}

TRACE_AUDIT_LINKING_RULES: dict[str, Any] = {
    "schema": "editor_tui_trace_audit_linking_v1",
    "linking_keys": ["trace_id", "task_id", "goal_id", "artifact_id", "approval_id"],
    "must_link": {
        "task_to_artifact": True,
        "task_to_approval_when_sensitive": True,
        "log_to_trace": True,
    },
    "kritis_evidence_ready": True,
}

ONBOARDING_SPLIT_MODEL: dict[str, Any] = {
    "schema": "editor_tui_onboarding_split_v1",
    "default_recommendations": {
        "coding_review": "nvim_plugin",
        "operations_admin": "tui",
    },
    "first_run_message": "Use plugin for coding workflows and TUI for operations workflows.",
}

RELEASE_SMOKE_STRATEGY: dict[str, Any] = {
    "schema": "editor_tui_release_smoke_strategy_v1",
    "targets": ["local_host", "containerized_target"],
    "smoke_flows": [
        "connect",
        "auth",
        "goal_submit_or_list",
        "task_list_and_detail",
        "artifact_list_and_detail",
    ],
    "release_rule": "core_flows_must_pass_before_claiming_usability",
}

NVIM_ARCHITECTURE_DECISION: dict[str, Any] = {
    "schema": "nvim_plugin_architecture_decision_v1",
    "support_strategy": "neovim_first_vim_followup",
    "reasoning": [
        "neovim_rpc_and_lua_support_simplify_first_release",
        "vim_compatibility_layer_can_be_added_incrementally",
        "avoid_overengineering_dual_runtime_from_day_one",
    ],
    "maintainability": "single_command_contract_and_shared_backend_api_client",
}

NVIM_EDITOR_MATRIX: dict[str, Any] = {
    "schema": "nvim_editor_matrix_v1",
    "supported_versions": {
        "neovim": ">=0.9",
        "vim": "planned_followup_after_neovim_first_release",
    },
    "dependencies": ["python3", "ananta_http_api_access"],
    "optional_dependencies": ["fzf_or_picker_for_quick_actions"],
}

NVIM_COMMAND_SURFACE: dict[str, Any] = {
    "schema": "nvim_command_surface_v1",
    "commands": [
        {"command": "AnantaGoalSubmit", "use_case": "goal_submission"},
        {"command": "AnantaAnalyze", "use_case": "analyze"},
        {"command": "AnantaReview", "use_case": "review"},
        {"command": "AnantaPatchPlan", "use_case": "patch"},
        {"command": "AnantaProjectNew", "use_case": "new_project"},
        {"command": "AnantaProjectEvolve", "use_case": "evolve_project"},
    ],
    "naming_style": "editor_native_pascal_commands",
}

TUI_FRAMEWORK_ARCHITECTURE_DECISION: dict[str, Any] = {
    "schema": "tui_framework_architecture_decision_v1",
    "selected_stack": "python_textual_first",
    "design_principles": [
        "terminal_native_navigation",
        "view_state_isolated_from_backend_orchestration",
        "long_lived_operations_sessions",
    ],
    "maintainability": "modular_views_with_shared_backend_client",
}

TUI_INFORMATION_ARCHITECTURE: dict[str, Any] = {
    "schema": "tui_information_architecture_v1",
    "primary_sections": [
        "dashboard",
        "tasks",
        "artifacts",
        "goals",
        "approvals",
        "logs",
        "kritis",
        "settings",
    ],
    "workflow_pattern": ["overview", "filter_or_search", "detail", "action_confirm"],
    "browser_handoff_supported": True,
}

TUI_GLOBAL_NAV_LAYOUT: dict[str, Any] = {
    "schema": "tui_global_navigation_layout_v1",
    "layout_regions": ["header", "sidebar", "main_content", "footer_shortcuts"],
    "default_shortcuts": {
        "next_view": "tab",
        "previous_view": "shift+tab",
        "refresh": "r",
        "open_detail": "enter",
        "back": "esc",
    },
    "small_terminal_degradation": "single_pane_stack_mode",
}


def _clean_text(value: Any, *, max_chars: int) -> str:
    text = str(value or "").strip()
    return text[: max(1, int(max_chars))]


def _compact_task_item(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _clean_text(task.get("id"), max_chars=100),
        "title": _clean_text(task.get("title"), max_chars=200),
        "status": _clean_text(task.get("status") or "todo", max_chars=40),
        "priority": _clean_text(task.get("priority") or "P1", max_chars=20),
    }


def _normalize_connection_profile(profile: dict[str, Any]) -> dict[str, Any]:
    profile_id = _clean_text(profile.get("id") or "default", max_chars=80)
    endpoint = _clean_text(profile.get("endpoint") or "http://localhost:8080", max_chars=240)
    environment = _clean_text(profile.get("environment") or "local", max_chars=40).lower()
    auth_mode = _clean_text(profile.get("auth_mode") or "session_token", max_chars=40).lower()
    role = _clean_text(profile.get("role") or "developer", max_chars=40).lower()
    return {
        "id": profile_id,
        "endpoint": endpoint,
        "environment": environment,
        "auth_mode": auth_mode,
        "role": role,
        "kritis_target": bool(profile.get("kritis_target", False)),
    }


def build_connection_profile_abstraction(
    profiles: list[dict[str, Any]],
    *,
    active_profile_id: str | None = None,
) -> dict[str, Any]:
    normalized_profiles = []
    seen_ids: set[str] = set()
    for profile in profiles or []:
        normalized = _normalize_connection_profile(profile)
        if normalized["id"] in seen_ids:
            continue
        seen_ids.add(normalized["id"])
        normalized_profiles.append(normalized)
    if not normalized_profiles:
        normalized_profiles.append(_normalize_connection_profile({}))
    active = _clean_text(active_profile_id or normalized_profiles[0]["id"], max_chars=80)
    if active not in {profile["id"] for profile in normalized_profiles}:
        active = normalized_profiles[0]["id"]
    return {
        "schema": "editor_tui_connection_profiles_v1",
        "profiles": normalized_profiles,
        "active_profile_id": active,
        "supports_multiple_instances": True,
    }


def build_nvim_connection_auth_support(
    profile: dict[str, Any],
    *,
    token: str | None = None,
) -> dict[str, Any]:
    normalized = _normalize_connection_profile(profile)
    raw_token = _clean_text(token or "", max_chars=400)
    token_present = bool(raw_token)
    token_preview = "***" if token_present else None
    return {
        "schema": "nvim_connection_auth_support_v1",
        "connection_profile": normalized,
        "auth": {
            "mode": normalized["auth_mode"],
            "token_present": token_present,
            "token_preview": token_preview,
            "secret_logged": False,
            "secret_exposed_to_command_history": False,
        },
    }


def collect_nvim_editor_context(
    editor_state: dict[str, Any],
    *,
    max_selection_chars: int = 2000,
    max_buffer_chars: int = 4000,
) -> dict[str, Any]:
    file_path = _clean_text(editor_state.get("file_path"), max_chars=400)
    project_root = _clean_text(editor_state.get("project_root"), max_chars=400)
    filetype = _clean_text(editor_state.get("filetype"), max_chars=80)
    selection_text = _clean_text(editor_state.get("selection_text"), max_chars=max_selection_chars)
    buffer_text = _clean_text(editor_state.get("buffer_text"), max_chars=max_buffer_chars)
    return {
        "schema": "nvim_editor_context_v1",
        "file_path": file_path or None,
        "project_root": project_root or None,
        "filetype": filetype or None,
        "cursor": {
            "line": max(1, int(editor_state.get("cursor_line", 1) or 1)),
            "column": max(1, int(editor_state.get("cursor_column", 1) or 1)),
        },
        "selection": {
            "present": bool(selection_text),
            "size_chars": len(selection_text),
            "text": selection_text or None,
        },
        "buffer_excerpt": {
            "size_chars": len(buffer_text),
            "text": buffer_text or None,
        },
        "bounded": True,
    }


def build_nvim_goal_submission_flow(
    *,
    goal_text: str,
    editor_context: dict[str, Any],
    include_selection: bool = True,
) -> dict[str, Any]:
    goal = _clean_text(goal_text, max_chars=600)
    selection = dict(editor_context.get("selection") or {})
    payload_context = {
        "file_path": editor_context.get("file_path"),
        "project_root": editor_context.get("project_root"),
        "cursor": dict(editor_context.get("cursor") or {}),
        "buffer_excerpt": dict(editor_context.get("buffer_excerpt") or {}),
    }
    if include_selection and bool(selection.get("present")):
        payload_context["selection"] = selection
    return {
        "schema": "nvim_goal_submission_flow_v1",
        "goal_text": goal,
        "use_case_alignment": ["goal_submission", "analyze", "review"],
        "context_payload": payload_context,
        "explicit_user_trigger_required": True,
    }


def build_nvim_quick_action_palette() -> dict[str, Any]:
    action_specs = [
        ("quick_goal_submit", "AnantaGoalSubmit", "Submit goal from context"),
        ("quick_analyze_file", "AnantaAnalyze", "Analyze current file"),
        ("quick_review_selection", "AnantaReview", "Review current selection"),
        ("quick_patch_plan", "AnantaPatchPlan", "Generate patch plan"),
        ("quick_project_new", "AnantaProjectNew", "Start new project path"),
        ("quick_project_evolve", "AnantaProjectEvolve", "Evolve existing project"),
    ]
    actions = [
        {"id": item_id, "command": command, "label": label, "trigger_mode": "command_or_picker"}
        for item_id, command, label in action_specs
    ]
    return {
        "schema": "nvim_quick_action_palette_v1",
        "actions": actions,
        "lightweight_picker_supported": True,
        "memorization_heavy": False,
    }


def build_nvim_analyze_flow(
    *,
    editor_context: dict[str, Any],
    scope: str = "current_file",
) -> dict[str, Any]:
    effective_scope = scope if scope in {"current_file", "project"} else "current_file"
    return {
        "schema": "nvim_analyze_flow_v1",
        "scope": effective_scope,
        "trigger_command": "AnantaAnalyze",
        "context_payload": {
            "file_path": editor_context.get("file_path"),
            "project_root": editor_context.get("project_root"),
            "cursor": dict(editor_context.get("cursor") or {}),
        },
        "editor_native_result_views": ["scratch_buffer", "floating_window", "location_list"],
        "requires_editor_exit": False,
    }


def build_nvim_review_flow(
    *,
    editor_context: dict[str, Any],
    review_prompt: str | None = None,
) -> dict[str, Any]:
    selection = dict(editor_context.get("selection") or {})
    return {
        "schema": "nvim_review_flow_v1",
        "trigger_command": "AnantaReview",
        "review_prompt": _clean_text(review_prompt or "Review selected code", max_chars=240),
        "selection_required": False,
        "selection_payload": selection,
        "safe_bounded_context_submission": True,
        "result_anchor": {
            "file_path": editor_context.get("file_path"),
            "cursor_line": dict(editor_context.get("cursor") or {}).get("line"),
        },
    }


def build_nvim_patch_planning_flow(
    *,
    editor_context: dict[str, Any],
    issue_summary: str | None = None,
) -> dict[str, Any]:
    return {
        "schema": "nvim_patch_planning_flow_v1",
        "trigger_command": "AnantaPatchPlan",
        "issue_summary": _clean_text(issue_summary or "", max_chars=280) or None,
        "proposal_only": True,
        "auto_apply": False,
        "context_payload": {
            "file_path": editor_context.get("file_path"),
            "project_root": editor_context.get("project_root"),
            "selection": dict(editor_context.get("selection") or {}),
        },
        "clear_user_control_required": True,
    }


def build_nvim_task_context_view(
    tasks: list[dict[str, Any]],
    *,
    current_project: str | None = None,
    max_items: int = 20,
) -> dict[str, Any]:
    compact = [_compact_task_item(task) for task in tasks[: max(1, int(max_items))]]
    return {
        "schema": "nvim_task_context_view_v1",
        "project_root": current_project,
        "items": compact,
        "compact_navigation": True,
        "editor_exit_required": False,
    }


def build_nvim_artifact_preview(
    artifact: dict[str, Any],
    *,
    max_text_chars: int = 4000,
) -> dict[str, Any]:
    artifact_type = _clean_text(artifact.get("type") or "text", max_chars=60).lower()
    raw_content = _clean_text(artifact.get("content"), max_chars=max_text_chars)
    supported = artifact_type in {"text", "markdown", "diff", "json"}
    return {
        "schema": "nvim_artifact_preview_v1",
        "artifact_id": _clean_text(artifact.get("id"), max_chars=80),
        "artifact_type": artifact_type,
        "supported_in_editor": supported,
        "preview_content": raw_content if supported else None,
        "degraded_behavior": "show_metadata_and_offer_open_in_browser" if not supported else "none",
    }


def build_nvim_context_inspection_panel(editor_context: dict[str, Any]) -> dict[str, Any]:
    selection = dict(editor_context.get("selection") or {})
    return {
        "schema": "nvim_context_inspection_panel_v1",
        "visible_fields": {
            "file_path": editor_context.get("file_path"),
            "project_root": editor_context.get("project_root"),
            "selection_size_chars": int(selection.get("size_chars") or 0),
            "buffer_excerpt_size_chars": int(dict(editor_context.get("buffer_excerpt") or {}).get("size_chars") or 0),
        },
        "can_trim_context": True,
        "can_cancel_submission": True,
    }


def build_nvim_diff_proposal_render(
    *,
    proposals: list[dict[str, Any]],
    max_hunks_per_proposal: int = 8,
) -> dict[str, Any]:
    rendered = []
    for proposal in proposals or []:
        hunks = list(proposal.get("hunks") or [])[: max(1, int(max_hunks_per_proposal))]
        rendered.append(
            {
                "proposal_id": _clean_text(proposal.get("id"), max_chars=100),
                "title": _clean_text(proposal.get("title"), max_chars=200),
                "hunks": hunks,
                "apply_requires_explicit_confirmation": True,
            }
        )
    return {
        "schema": "nvim_diff_proposal_render_v1",
        "proposals": rendered,
        "review_first_posture": True,
    }


def build_nvim_navigation_links(
    *,
    task_item: dict[str, Any],
    artifact_items: list[dict[str, Any]],
    source_locations: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema": "nvim_navigation_links_v1",
        "task": _compact_task_item(task_item),
        "artifact_links": [
            {
                "artifact_id": _clean_text(item.get("id"), max_chars=100),
                "title": _clean_text(item.get("title"), max_chars=180),
            }
            for item in artifact_items or []
        ],
        "source_links": [
            {
                "file_path": _clean_text(link.get("file_path"), max_chars=400),
                "line": max(1, int(link.get("line", 1) or 1)),
            }
            for link in source_locations or []
        ],
        "predictable_navigation": True,
    }


def build_nvim_external_shortcuts(
    *,
    base_url: str,
    task_id: str | None = None,
    artifact_id: str | None = None,
    goal_id: str | None = None,
) -> dict[str, Any]:
    clean_base = _clean_text(base_url, max_chars=240).rstrip("/")
    shortcuts = []
    if task_id:
        shortcuts.append({"label": "Open task in browser", "url": f"{clean_base}/tasks/{_clean_text(task_id, max_chars=100)}"})
    if artifact_id:
        shortcuts.append(
            {"label": "Open artifact in browser", "url": f"{clean_base}/artifacts/{_clean_text(artifact_id, max_chars=100)}"}
        )
    if goal_id:
        shortcuts.append({"label": "Open goal in browser", "url": f"{clean_base}/goals/{_clean_text(goal_id, max_chars=100)}"})
    return {
        "schema": "nvim_external_shortcuts_v1",
        "shortcuts": shortcuts,
        "optional_not_forced": True,
    }


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
    selected = _clean_text(selected_artifact_id, max_chars=100) if selected_artifact_id else (items[0]["id"] if items else None)
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
    filters = [_clean_text(status, max_chars=30) for status in (status_filters or ["todo", "in_progress", "blocked", "done"])]
    effective_group = group_by if group_by in {"status", "priority"} else "status"
    return {
        "schema": "tui_task_filtering_grouping_v1",
        "base_task_board_schema": _clean_text(task_board.get("schema"), max_chars=80),
        "supported_filters": filters,
        "grouping_modes": ["status", "priority"],
        "default_grouping": effective_group,
    }


def build_editor_tui_foundation_snapshot(
    *,
    connection_profiles: list[dict[str, Any]],
    active_profile_id: str | None,
    nvim_editor_state: dict[str, Any],
    nvim_goal_text: str,
    nvim_tasks: list[dict[str, Any]] | None = None,
    nvim_artifact: dict[str, Any] | None = None,
    nvim_diff_proposals: list[dict[str, Any]] | None = None,
    nvim_source_locations: list[dict[str, Any]] | None = None,
    tui_tasks: list[dict[str, Any]] | None = None,
    tui_artifacts: list[dict[str, Any]] | None = None,
    tui_goals: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    profile_model = build_connection_profile_abstraction(
        connection_profiles,
        active_profile_id=active_profile_id,
    )
    active_profile = next(
        (profile for profile in profile_model["profiles"] if profile["id"] == profile_model["active_profile_id"]),
        profile_model["profiles"][0],
    )
    nvim_context = collect_nvim_editor_context(nvim_editor_state)
    nvim_goal_submission = build_nvim_goal_submission_flow(
        goal_text=nvim_goal_text,
        editor_context=nvim_context,
        include_selection=True,
    )
    nvim_task_view = build_nvim_task_context_view(
        nvim_tasks or [],
        current_project=nvim_context.get("project_root"),
    )
    nvim_artifact_preview = build_nvim_artifact_preview(
        nvim_artifact or {"id": "artifact-preview", "type": "text", "content": ""},
    )
    nvim_navigation = build_nvim_navigation_links(
        task_item=(nvim_tasks or [{"id": "task-0", "title": "No task yet", "status": "todo", "priority": "P1"}])[0],
        artifact_items=[nvim_artifact or {"id": "artifact-preview", "title": "Preview", "type": "text"}],
        source_locations=nvim_source_locations or [],
    )
    tui_task_board = build_tui_task_board_view(tui_tasks or [])
    return {
        "schema": "editor_tui_foundation_snapshot_v1",
        "shared_ui_boundary_model": dict(SHARED_UI_BOUNDARY_MODEL),
        "frontend_contract_inventory": dict(FRONTEND_CONTRACT_INVENTORY),
        "common_object_model": dict(COMMON_OBJECT_MODEL),
        "connection_profile_abstraction": profile_model,
        "capability_surface_model": dict(CAPABILITY_SURFACE_MODEL),
        "error_degraded_state_model": dict(ERROR_DEGRADED_STATE_MODEL),
        "trace_audit_linking_rules": dict(TRACE_AUDIT_LINKING_RULES),
        "onboarding_split_model": dict(ONBOARDING_SPLIT_MODEL),
        "release_smoke_strategy": dict(RELEASE_SMOKE_STRATEGY),
        "nvim_plugin_architecture": dict(NVIM_ARCHITECTURE_DECISION),
        "nvim_editor_matrix": dict(NVIM_EDITOR_MATRIX),
        "nvim_connection_auth_support": build_nvim_connection_auth_support(active_profile),
        "nvim_command_surface": dict(NVIM_COMMAND_SURFACE),
        "nvim_quick_action_palette": build_nvim_quick_action_palette(),
        "nvim_editor_context": nvim_context,
        "nvim_goal_submission_flow": nvim_goal_submission,
        "nvim_analyze_flow": build_nvim_analyze_flow(editor_context=nvim_context),
        "nvim_review_flow": build_nvim_review_flow(editor_context=nvim_context),
        "nvim_patch_planning_flow": build_nvim_patch_planning_flow(editor_context=nvim_context),
        "nvim_task_context_view": nvim_task_view,
        "nvim_artifact_preview": nvim_artifact_preview,
        "nvim_context_inspection_panel": build_nvim_context_inspection_panel(nvim_context),
        "nvim_diff_proposal_render": build_nvim_diff_proposal_render(proposals=nvim_diff_proposals or []),
        "nvim_navigation_links": nvim_navigation,
        "nvim_external_shortcuts": build_nvim_external_shortcuts(
            base_url=active_profile["endpoint"],
            task_id=nvim_navigation["task"]["id"],
            artifact_id=nvim_artifact_preview["artifact_id"],
        ),
        "tui_framework_architecture": dict(TUI_FRAMEWORK_ARCHITECTURE_DECISION),
        "tui_information_architecture": dict(TUI_INFORMATION_ARCHITECTURE),
        "tui_auth_session_support": build_tui_auth_session_support(active_profile),
        "tui_global_navigation_layout": dict(TUI_GLOBAL_NAV_LAYOUT),
        "tui_runtime_status_header": build_tui_runtime_status_header(active_profile),
        "tui_task_board_view": tui_task_board,
        "tui_task_detail_view": build_tui_task_detail_view(
            (tui_tasks or [{"id": "task-0", "title": "No task yet", "status": "todo", "priority": "P1"}])[0],
            artifacts=tui_artifacts or [],
            routing_hints=["hub_worker_orchestration_path"],
        ),
        "tui_artifact_view": build_tui_artifact_view(tui_artifacts or []),
        "tui_goal_view": build_tui_goal_view(tui_goals or []),
        "tui_goal_submission_entry": build_tui_goal_submission_entry(),
        "tui_task_filtering_grouping": build_tui_task_filtering_grouping(task_board=tui_task_board),
    }
