from agent.services.editor_tui_surface_foundation_service import (
    build_connection_profile_abstraction,
    build_editor_tui_foundation_snapshot,
    build_nvim_analyze_flow,
    build_nvim_approval_awareness_view,
    build_nvim_artifact_preview,
    build_nvim_blueprint_project_start_commands,
    build_nvim_connection_auth_support,
    build_nvim_context_inspection_panel,
    build_nvim_diff_proposal_render,
    build_nvim_external_shortcuts,
    build_nvim_first_run_guided_setup,
    build_nvim_goal_submission_flow,
    build_nvim_knowledge_context_source_summary,
    build_nvim_navigation_links,
    build_nvim_patch_planning_flow,
    build_nvim_quick_action_palette,
    build_nvim_review_flow,
    build_nvim_task_context_view,
    build_nvim_trace_diagnostic_summary,
    build_tui_approval_action_flow,
    build_tui_approval_queue_view,
    build_tui_artifact_view,
    build_tui_audit_summary_view,
    build_tui_audit_trace_drilldown,
    build_tui_auth_session_support,
    build_tui_cross_view_search_filtering,
    build_tui_empty_error_state_ux,
    build_tui_goal_submission_entry,
    build_tui_goal_view,
    build_tui_health_runtime_diagnostics_view,
    build_tui_keyboard_navigation_refinement,
    build_tui_kritis_dashboard_summary,
    build_tui_log_stream_view,
    build_tui_policy_denial_view,
    build_tui_provider_backend_visibility_view,
    build_tui_repair_approval_execution_review,
    build_tui_repair_session_views,
    build_tui_runtime_status_header,
    build_tui_state_persistence_resume,
    build_tui_task_board_view,
    build_tui_task_detail_view,
    build_tui_task_filtering_grouping,
    collect_nvim_editor_context,
)


def test_build_connection_profile_abstraction_normalizes_and_deduplicates() -> None:
    model = build_connection_profile_abstraction(
        [
            {
                "id": "dev-local",
                "endpoint": "http://localhost:8080",
                "environment": "Local",
                "auth_mode": "Session_Token",
                "role": "Developer",
            },
            {
                "id": "dev-local",
                "endpoint": "http://ignored-duplicate:8080",
            },
            {
                "id": "kritis-stage",
                "endpoint": "https://kritis-stage.internal",
                "environment": "staging",
                "auth_mode": "oauth",
                "role": "operator",
                "kritis_target": True,
            },
        ],
        active_profile_id="kritis-stage",
    )

    assert model["schema"] == "editor_tui_connection_profiles_v1"
    assert model["active_profile_id"] == "kritis-stage"
    assert len(model["profiles"]) == 2
    assert model["profiles"][0]["environment"] == "local"
    assert model["profiles"][1]["kritis_target"] is True


def test_build_nvim_connection_auth_support_redacts_secret_preview() -> None:
    model = build_nvim_connection_auth_support(
        {
            "id": "dev-local",
            "endpoint": "http://localhost:8080",
            "auth_mode": "session_token",
        },
        token="super-secret-value",
    )

    assert model["auth"]["token_present"] is True
    assert model["auth"]["token_preview"] == "***"
    assert model["auth"]["secret_logged"] is False
    assert model["auth"]["secret_exposed_to_command_history"] is False


def test_collect_nvim_editor_context_is_bounded_and_keeps_required_fields() -> None:
    context = collect_nvim_editor_context(
        {
            "file_path": "/workspace/src/main.py",
            "project_root": "/workspace",
            "filetype": "python",
            "cursor_line": 12,
            "cursor_column": 7,
            "selection_text": "A" * 3000,
            "buffer_text": "B" * 5000,
        }
    )

    assert context["schema"] == "nvim_editor_context_v1"
    assert context["cursor"]["line"] == 12
    assert context["selection"]["present"] is True
    assert context["selection"]["size_chars"] == 2000
    assert context["buffer_excerpt"]["size_chars"] == 4000
    assert context["bounded"] is True


def test_nvim_core_workflow_contracts_cover_next_block() -> None:
    context = collect_nvim_editor_context(
        {
            "file_path": "/workspace/src/main.py",
            "project_root": "/workspace",
            "filetype": "python",
            "cursor_line": 5,
            "cursor_column": 2,
            "selection_text": "important_function()",
            "buffer_text": "def important_function(): pass",
        }
    )
    goal_flow = build_nvim_goal_submission_flow(goal_text="Review this function", editor_context=context)
    quick_actions = build_nvim_quick_action_palette()
    analyze_flow = build_nvim_analyze_flow(editor_context=context, scope="project")
    review_flow = build_nvim_review_flow(editor_context=context)
    patch_flow = build_nvim_patch_planning_flow(editor_context=context, issue_summary="Fix bug in parser")

    assert goal_flow["schema"] == "nvim_goal_submission_flow_v1"
    assert goal_flow["goal_text"] == "Review this function"
    assert goal_flow["context_payload"]["selection"]["present"] is True
    assert quick_actions["schema"] == "nvim_quick_action_palette_v1"
    assert len(quick_actions["actions"]) == 6
    assert analyze_flow["schema"] == "nvim_analyze_flow_v1"
    assert analyze_flow["scope"] == "project"
    assert review_flow["schema"] == "nvim_review_flow_v1"
    assert review_flow["safe_bounded_context_submission"] is True
    assert patch_flow["schema"] == "nvim_patch_planning_flow_v1"
    assert patch_flow["auto_apply"] is False


def test_nvim_views_cover_task_artifact_context_and_navigation() -> None:
    context = collect_nvim_editor_context(
        {
            "file_path": "/workspace/src/main.py",
            "project_root": "/workspace",
            "filetype": "python",
            "cursor_line": 9,
            "cursor_column": 4,
            "selection_text": "x = compute()",
            "buffer_text": "x = compute()",
        }
    )
    task_view = build_nvim_task_context_view(
        [
            {"id": "T-1", "title": "Review parser", "status": "in_progress", "priority": "P0"},
            {"id": "T-2", "title": "Check logs", "status": "todo", "priority": "P1"},
        ],
        current_project="/workspace",
    )
    unsupported_artifact_preview = build_nvim_artifact_preview(
        {"id": "A-1", "type": "binary", "content": "n/a"},
    )
    inspection_panel = build_nvim_context_inspection_panel(context)
    diff_render = build_nvim_diff_proposal_render(
        proposals=[{"id": "P-1", "title": "Fix parser", "hunks": [{"path": "src/main.py", "line": 10}]}]
    )
    nav = build_nvim_navigation_links(
        task_item={"id": "T-1", "title": "Review parser", "status": "in_progress", "priority": "P0"},
        artifact_items=[{"id": "A-1", "title": "Parser report", "type": "markdown"}],
        source_locations=[{"file_path": "/workspace/src/main.py", "line": 10}],
    )
    shortcuts = build_nvim_external_shortcuts(
        base_url="http://localhost:8080",
        task_id="T-1",
        artifact_id="A-1",
        goal_id="G-1",
    )

    assert task_view["schema"] == "nvim_task_context_view_v1"
    assert len(task_view["items"]) == 2
    assert unsupported_artifact_preview["supported_in_editor"] is False
    assert unsupported_artifact_preview["degraded_behavior"] == "show_metadata_and_offer_open_in_browser"
    assert inspection_panel["schema"] == "nvim_context_inspection_panel_v1"
    assert inspection_panel["can_trim_context"] is True
    assert diff_render["schema"] == "nvim_diff_proposal_render_v1"
    assert diff_render["proposals"][0]["apply_requires_explicit_confirmation"] is True
    assert nav["schema"] == "nvim_navigation_links_v1"
    assert nav["source_links"][0]["line"] == 10
    assert shortcuts["schema"] == "nvim_external_shortcuts_v1"
    assert len(shortcuts["shortcuts"]) == 3


def test_nvim_advanced_contracts_cover_stage_four_scope() -> None:
    blueprint = build_nvim_blueprint_project_start_commands(
        blueprint_options=["api_service"],
        work_profiles=["product_fast_line", "kritis_line"],
    )
    approval = build_nvim_approval_awareness_view(
        operation_id="op-7",
        approval_state="blocked",
        policy_reason="approval required",
    )
    trace_summary = build_nvim_trace_diagnostic_summary(
        [{"trace_id": "trace-1", "route": "hub->worker", "status": "ok"}],
    )
    knowledge = build_nvim_knowledge_context_source_summary(
        [{"source_class": "repo", "label": "src/service.py", "weight": 0.7}],
    )
    setup = build_nvim_first_run_guided_setup(recommended_use_case="review")

    assert blueprint["schema"] == "nvim_blueprint_project_start_commands_v1"
    assert len(blueprint["commands"]) == 2
    assert approval["schema"] == "nvim_approval_awareness_view_v1"
    assert approval["blocked_by_policy"] is True
    assert trace_summary["schema"] == "nvim_trace_diagnostic_summary_v1"
    assert trace_summary["optional_advanced_view"] is True
    assert knowledge["schema"] == "nvim_knowledge_context_source_summary_v1"
    assert knowledge["compact_and_understandable"] is True
    assert setup["schema"] == "nvim_first_run_guided_setup_v1"
    assert setup["browser_required_for_first_success"] is False


def test_tui_foundation_and_core_views_cover_next_block() -> None:
    profile = {
        "id": "ops-local",
        "endpoint": "http://localhost:8080",
        "environment": "local",
        "auth_mode": "session_token",
        "role": "operator",
    }
    auth = build_tui_auth_session_support(profile, auth_state="authenticated", session_ttl_minutes=180)
    header = build_tui_runtime_status_header(profile, connection_state="connected", health_state="ok")
    board = build_tui_task_board_view(
        [
            {"id": "T-1", "title": "Investigate", "status": "todo", "priority": "P1"},
            {"id": "T-2", "title": "Repair", "status": "in_progress", "priority": "P0"},
        ],
        group_by="priority",
    )
    detail = build_tui_task_detail_view(
        {"id": "T-2", "title": "Repair", "status": "in_progress", "priority": "P0", "summary": "Repair issue"},
        artifacts=[{"id": "A-1", "title": "Repair plan", "type": "markdown"}],
        routing_hints=["hub_worker_orchestration_path"],
    )
    artifact_view = build_tui_artifact_view(
        [{"id": "A-1", "title": "Repair plan", "type": "markdown"}],
        selected_artifact_id="A-1",
    )
    goal_view = build_tui_goal_view([{"id": "G-1", "title": "Repair host", "status": "open"}], allow_submission=True)
    goal_entry = build_tui_goal_submission_entry(quick_actions=["analyze", "review"])
    filtering = build_tui_task_filtering_grouping(task_board=board, status_filters=["todo", "in_progress"])

    assert auth["schema"] == "tui_auth_session_support_v1"
    assert auth["long_lived_terminal_safe"] is True
    assert header["schema"] == "tui_runtime_status_header_v1"
    assert header["compact_and_visible"] is True
    assert board["schema"] == "tui_task_board_view_v1"
    assert board["group_by"] == "priority"
    assert detail["schema"] == "tui_task_detail_view_v1"
    assert detail["terminal_readable"] is True
    assert artifact_view["schema"] == "tui_artifact_view_v1"
    assert artifact_view["selected_artifact"]["id"] == "A-1"
    assert goal_view["schema"] == "tui_goal_view_v1"
    assert goal_view["goal_submission_available"] is True
    assert goal_entry["schema"] == "tui_goal_submission_entry_v1"
    assert len(goal_entry["quick_actions"]) == 2
    assert filtering["schema"] == "tui_task_filtering_grouping_v1"
    assert filtering["default_grouping"] == "status"


def test_tui_advanced_contracts_cover_logs_approval_kritis_and_usability() -> None:
    logs = build_tui_log_stream_view(
        [{"id": "L-1", "level": "warn", "message": "latency high", "timestamp": "2026-04-24T15:00:00Z"}],
        level_filter="warn",
    )
    queue = build_tui_approval_queue_view(
        [{"id": "AP-1", "scope": "repair", "state": "pending", "context_summary": "repair step"}],
    )
    action = build_tui_approval_action_flow(
        {"id": "AP-1"},
        action="approve",
        operator_note="reviewed context",
    )
    audit = build_tui_audit_summary_view(
        [{"chain_id": "C-1", "risk_level": "high", "headline": "repair chain"}],
    )
    drilldown = build_tui_audit_trace_drilldown(
        trace_id="trace-1",
        events=[{"event_id": "E-1", "message": "approved"}],
        redaction_applied=True,
    )
    denials = build_tui_policy_denial_view(
        [{"action_id": "A-9", "reason": "policy denied", "policy": "approval_policy"}],
    )
    kritis = build_tui_kritis_dashboard_summary(
        audit_health="healthy",
        approval_backlog=2,
        mutation_status="guarded",
        policy_posture="strict",
    )
    repair_sessions = build_tui_repair_session_views(
        [{"session_id": "R-1", "diagnosis": "disk full", "state": "planned", "verification_result": "pending"}],
    )
    repair_flow = build_tui_repair_approval_execution_review(
        repair_session_id="R-1",
        dry_run_supported=True,
        approval_required=True,
    )
    health = build_tui_health_runtime_diagnostics_view(
        health="ok",
        readiness="ready",
        diagnostics=[{"key": "workers", "value": "3"}],
    )
    providers = build_tui_provider_backend_visibility_view(
        [{"provider": "openai", "backend": "api", "capability_state": "enabled"}],
    )
    keyboard = build_tui_keyboard_navigation_refinement()
    search = build_tui_cross_view_search_filtering()
    resume = build_tui_state_persistence_resume(selected_profile_id="ops-local", last_view="approvals")
    empty_error = build_tui_empty_error_state_ux()

    assert logs["schema"] == "tui_log_stream_view_v1"
    assert logs["active_level_filter"] == "warn"
    assert queue["schema"] == "tui_approval_queue_view_v1"
    assert action["schema"] == "tui_approval_action_flow_v1"
    assert action["action_allowed"] is True
    assert audit["schema"] == "tui_audit_summary_view_v1"
    assert drilldown["schema"] == "tui_audit_trace_drilldown_v1"
    assert denials["schema"] == "tui_policy_denial_view_v1"
    assert kritis["schema"] == "tui_kritis_dashboard_summary_v1"
    assert repair_sessions["schema"] == "tui_repair_session_views_v1"
    assert repair_flow["schema"] == "tui_repair_approval_execution_review_v1"
    assert health["schema"] == "tui_health_runtime_diagnostics_view_v1"
    assert providers["schema"] == "tui_provider_backend_visibility_view_v1"
    assert keyboard["schema"] == "tui_keyboard_navigation_refinement_v1"
    assert search["schema"] == "tui_cross_view_search_filtering_v1"
    assert resume["schema"] == "tui_state_persistence_resume_v1"
    assert empty_error["schema"] == "tui_empty_error_state_ux_v1"


def test_build_editor_tui_foundation_snapshot_contains_next_20_task_contracts() -> None:
    snapshot = build_editor_tui_foundation_snapshot(
        connection_profiles=[
            {
                "id": "dev-local",
                "endpoint": "http://localhost:8080",
                "environment": "local",
                "auth_mode": "session_token",
            }
        ],
        active_profile_id="dev-local",
        nvim_editor_state={
            "file_path": "/workspace/src/main.py",
            "project_root": "/workspace",
            "filetype": "python",
            "cursor_line": 3,
            "cursor_column": 1,
            "selection_text": "print('hello')",
            "buffer_text": "print('hello')",
        },
        nvim_goal_text="Analyze current file",
        nvim_tasks=[{"id": "NT-1", "title": "Analyze parser", "status": "todo", "priority": "P0"}],
        nvim_artifact={"id": "NA-1", "title": "Review summary", "type": "markdown", "content": "good"},
        nvim_diff_proposals=[{"id": "NP-1", "title": "Patch parser", "hunks": [{"path": "src/main.py", "line": 4}]}],
        nvim_source_locations=[{"file_path": "/workspace/src/main.py", "line": 4}],
        nvim_traces=[{"trace_id": "trace-1", "route": "hub->worker", "status": "ok"}],
        nvim_knowledge_sources=[{"source_class": "repo", "label": "src/main.py", "weight": 0.8}],
        tui_tasks=[{"id": "TT-1", "title": "Inspect logs", "status": "todo", "priority": "P1"}],
        tui_artifacts=[{"id": "TA-1", "title": "Log bundle", "type": "text"}],
        tui_goals=[{"id": "TG-1", "title": "Stabilize service", "status": "open"}],
        tui_logs=[{"id": "L-1", "level": "info", "message": "startup", "timestamp": "2026-04-24T15:00:00Z"}],
        tui_approvals=[{"id": "AP-1", "scope": "repair", "state": "pending", "context_summary": "repair plan"}],
        tui_audit_chains=[{"event_id": "E-1", "message": "audit event"}],
        tui_policy_denials=[{"action_id": "A-1", "reason": "blocked", "policy": "approval_policy"}],
        tui_repair_sessions=[
            {
                "session_id": "R-1",
                "diagnosis": "disk full",
                "state": "planned",
                "verification_result": "pending",
            }
        ],
        tui_diagnostics=[{"key": "workers", "value": "3"}],
        tui_providers=[{"provider": "openai", "backend": "api", "capability_state": "enabled"}],
    )

    assert snapshot["schema"] == "editor_tui_foundation_snapshot_v1"
    assert snapshot["shared_ui_boundary_model"]["schema"] == "editor_tui_shared_boundary_v1"
    assert snapshot["frontend_contract_inventory"]["schema"] == "editor_tui_contract_inventory_v1"
    assert snapshot["common_object_model"]["schema"] == "editor_tui_common_object_model_v1"
    assert snapshot["capability_surface_model"]["schema"] == "editor_tui_capability_surface_v1"
    assert snapshot["error_degraded_state_model"]["schema"] == "editor_tui_error_degraded_model_v1"
    assert snapshot["trace_audit_linking_rules"]["schema"] == "editor_tui_trace_audit_linking_v1"
    assert snapshot["onboarding_split_model"]["schema"] == "editor_tui_onboarding_split_v1"
    assert snapshot["release_smoke_strategy"]["schema"] == "editor_tui_release_smoke_strategy_v1"
    assert snapshot["nvim_plugin_architecture"]["schema"] == "nvim_plugin_architecture_decision_v1"
    assert snapshot["nvim_editor_matrix"]["schema"] == "nvim_editor_matrix_v1"
    assert snapshot["nvim_connection_auth_support"]["schema"] == "nvim_connection_auth_support_v1"
    assert snapshot["nvim_command_surface"]["schema"] == "nvim_command_surface_v1"
    assert snapshot["nvim_quick_action_palette"]["schema"] == "nvim_quick_action_palette_v1"
    assert snapshot["nvim_analyze_flow"]["schema"] == "nvim_analyze_flow_v1"
    assert snapshot["nvim_review_flow"]["schema"] == "nvim_review_flow_v1"
    assert snapshot["nvim_patch_planning_flow"]["schema"] == "nvim_patch_planning_flow_v1"
    assert snapshot["nvim_task_context_view"]["schema"] == "nvim_task_context_view_v1"
    assert snapshot["nvim_artifact_preview"]["schema"] == "nvim_artifact_preview_v1"
    assert snapshot["nvim_context_inspection_panel"]["schema"] == "nvim_context_inspection_panel_v1"
    assert snapshot["nvim_diff_proposal_render"]["schema"] == "nvim_diff_proposal_render_v1"
    assert snapshot["nvim_navigation_links"]["schema"] == "nvim_navigation_links_v1"
    assert snapshot["nvim_external_shortcuts"]["schema"] == "nvim_external_shortcuts_v1"
    assert snapshot["nvim_blueprint_project_start_commands"]["schema"] == "nvim_blueprint_project_start_commands_v1"
    assert snapshot["nvim_approval_awareness_view"]["schema"] == "nvim_approval_awareness_view_v1"
    assert snapshot["nvim_trace_diagnostic_summary"]["schema"] == "nvim_trace_diagnostic_summary_v1"
    assert snapshot["nvim_knowledge_context_source_summary"]["schema"] == "nvim_knowledge_context_source_summary_v1"
    assert snapshot["nvim_first_run_guided_setup"]["schema"] == "nvim_first_run_guided_setup_v1"
    assert snapshot["tui_framework_architecture"]["schema"] == "tui_framework_architecture_decision_v1"
    assert snapshot["tui_information_architecture"]["schema"] == "tui_information_architecture_v1"
    assert snapshot["tui_auth_session_support"]["schema"] == "tui_auth_session_support_v1"
    assert snapshot["tui_global_navigation_layout"]["schema"] == "tui_global_navigation_layout_v1"
    assert snapshot["tui_runtime_status_header"]["schema"] == "tui_runtime_status_header_v1"
    assert snapshot["tui_task_board_view"]["schema"] == "tui_task_board_view_v1"
    assert snapshot["tui_task_detail_view"]["schema"] == "tui_task_detail_view_v1"
    assert snapshot["tui_artifact_view"]["schema"] == "tui_artifact_view_v1"
    assert snapshot["tui_goal_view"]["schema"] == "tui_goal_view_v1"
    assert snapshot["tui_goal_submission_entry"]["schema"] == "tui_goal_submission_entry_v1"
    assert snapshot["tui_task_filtering_grouping"]["schema"] == "tui_task_filtering_grouping_v1"
    assert snapshot["tui_log_stream_view"]["schema"] == "tui_log_stream_view_v1"
    assert snapshot["tui_approval_queue_view"]["schema"] == "tui_approval_queue_view_v1"
    assert snapshot["tui_approval_action_flow"]["schema"] == "tui_approval_action_flow_v1"
    assert snapshot["tui_audit_summary_view"]["schema"] == "tui_audit_summary_view_v1"
    assert snapshot["tui_audit_trace_drilldown"]["schema"] == "tui_audit_trace_drilldown_v1"
    assert snapshot["tui_policy_denial_view"]["schema"] == "tui_policy_denial_view_v1"
    assert snapshot["tui_kritis_dashboard_summary"]["schema"] == "tui_kritis_dashboard_summary_v1"
    assert snapshot["tui_repair_session_views"]["schema"] == "tui_repair_session_views_v1"
    assert snapshot["tui_repair_approval_execution_review"]["schema"] == "tui_repair_approval_execution_review_v1"
    assert snapshot["tui_health_runtime_diagnostics_view"]["schema"] == "tui_health_runtime_diagnostics_view_v1"
    assert snapshot["tui_provider_backend_visibility_view"]["schema"] == "tui_provider_backend_visibility_view_v1"
    assert snapshot["tui_keyboard_navigation_refinement"]["schema"] == "tui_keyboard_navigation_refinement_v1"
    assert snapshot["tui_cross_view_search_filtering"]["schema"] == "tui_cross_view_search_filtering_v1"
    assert snapshot["tui_state_persistence_resume"]["schema"] == "tui_state_persistence_resume_v1"
    assert snapshot["tui_empty_error_state_ux"]["schema"] == "tui_empty_error_state_ux_v1"
