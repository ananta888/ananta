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


def _clean_text(value: Any, *, max_chars: int) -> str:
    text = str(value or "").strip()
    return text[: max(1, int(max_chars))]


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


def build_editor_tui_foundation_snapshot(
    *,
    connection_profiles: list[dict[str, Any]],
    active_profile_id: str | None,
    nvim_editor_state: dict[str, Any],
    nvim_goal_text: str,
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
        "nvim_editor_context": nvim_context,
        "nvim_goal_submission_flow": nvim_goal_submission,
    }
