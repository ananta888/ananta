from agent.services.eclipse_plugin_adapter_foundation_service import (
    build_eclipse_artifact_view,
    build_eclipse_basic_task_detail_view,
    build_eclipse_blueprint_work_profile_view,
    build_eclipse_connection_auth_support,
    build_eclipse_connection_runtime_status_view,
    build_eclipse_context_inspection_view,
    build_eclipse_context_packaging_rules,
    build_eclipse_diff_review_render,
    build_eclipse_error_degraded_mode,
    build_eclipse_first_run_ux,
    build_eclipse_future_roadmap,
    build_eclipse_goal_input_panel,
    build_eclipse_goal_quick_action_view,
    build_eclipse_golden_path_demo,
    build_eclipse_health_capability_handshake,
    build_eclipse_manual_smoke_checklist,
    build_eclipse_mcp_integration_evaluation,
    build_eclipse_minimum_support_matrix,
    build_eclipse_open_in_browser_shortcuts,
    build_eclipse_openai_fallback_evaluation,
    build_eclipse_plugin_adapter_foundation_snapshot,
    build_eclipse_review_approval_action_support,
    build_eclipse_review_proposal_view,
    build_eclipse_security_privacy_guardrails,
    build_eclipse_selection_editor_handoff,
    build_eclipse_sgpt_cli_operation_bridge,
    build_eclipse_task_artifact_view,
    build_eclipse_task_list_view,
    build_eclipse_task_refresh_flow,
    build_eclipse_trace_visibility,
    build_eclipse_view_navigation_linking_model,
    build_eclipse_view_strategy,
    collect_eclipse_workspace_project_context,
)


def test_eclipse_support_and_connection_contracts() -> None:
    matrix = build_eclipse_minimum_support_matrix(
        eclipse_distribution="eclipse_ide_2024_09_plus",
        java_baseline="21",
        required_dependencies=["pde", "egit"],
    )
    auth = build_eclipse_connection_auth_support(
        {"id": "dev-local", "base_url": "http://localhost:8080", "auth_method": "session_token"},
        token="secret",
        canonical_auth_enabled=True,
    )
    handshake = build_eclipse_health_capability_handshake(
        health_payload={"state": "ready"},
        capabilities_payload={"capabilities": ["goals", "tasks"]},
    )

    assert matrix["schema"] == "eclipse_minimum_support_matrix_v1"
    assert matrix["java_baseline"] == "21"
    assert auth["schema"] == "eclipse_connection_auth_support_v1"
    assert auth["auth"]["token_preview"] == "***"
    assert auth["auth"]["token_logged"] is False
    assert handshake["schema"] == "eclipse_health_capability_handshake_v1"
    assert handshake["connected"] is True
    assert handshake["ui_connection_state"] == "connected"


def test_workspace_context_and_editor_handoff_are_bounded() -> None:
    workspace = collect_eclipse_workspace_project_context(
        {
            "workspace_path": "/workspace",
            "project_name": "demo",
            "active_file_path": "/workspace/src/main.py",
            "selected_paths": [f"/workspace/file_{idx}.py" for idx in range(60)],
        },
        max_paths=20,
    )
    handoff = build_eclipse_selection_editor_handoff(
        {
            "file_path": "/workspace/src/main.py",
            "selection_text": "A" * 7000,
            "file_content_excerpt": "B" * 7000,
        },
        max_chars=1000,
    )

    assert workspace["schema"] == "eclipse_workspace_project_context_v1"
    assert len(workspace["selected_paths"]) == 20
    assert workspace["bounded"] is True
    assert handoff["schema"] == "eclipse_selection_editor_handoff_v1"
    assert handoff["scope"] == "selection"
    assert handoff["clipped"] is True
    assert handoff["safe_bounded_payload"] is True


def test_goal_panel_task_artifact_view_and_refresh_flow_contracts() -> None:
    workspace = collect_eclipse_workspace_project_context(
        {"workspace_path": "/workspace", "project_name": "demo", "active_file_path": "/workspace/src/main.py"}
    )
    goal_panel = build_eclipse_goal_input_panel(
        goal_text="Review this module",
        workspace_context=workspace,
        selected_preset="change_review",
    )
    task_artifact = build_eclipse_task_artifact_view(
        tasks=[{"id": "T-1", "title": "Review task", "status": "todo", "review_required": True}],
        artifacts=[{"id": "A-1", "title": "Review output", "type": "markdown", "task_id": "T-1"}],
    )
    refresh = build_eclipse_task_refresh_flow(["T-1"], poll_interval_seconds=20)

    assert goal_panel["schema"] == "eclipse_goal_input_panel_v1"
    assert goal_panel["selected_preset"] == "change_review"
    assert goal_panel["official_paths_preferred"] is True
    assert task_artifact["schema"] == "eclipse_task_artifact_view_v1"
    assert task_artifact["tasks"][0]["review_required"] is True
    assert task_artifact["task_detail_view_available"] is True
    assert refresh["schema"] == "eclipse_task_refresh_flow_v1"
    assert refresh["poll_interval_seconds"] == 20
    assert refresh["lightweight_polling"] is True


def test_review_render_approval_actions_shortcuts_and_bridge_contracts() -> None:
    review = build_eclipse_diff_review_render(
        [{"id": "P-1", "title": "Patch proposal", "hunks": [{"path": "src/main.py", "line": 10}]}],
        max_hunks=5,
    )
    approval = build_eclipse_review_approval_action_support(
        review_item_id="R-1",
        action="approve",
        policy_allows=True,
    )
    shortcuts = build_eclipse_open_in_browser_shortcuts(
        base_url="http://localhost:8080",
        task_id="T-1",
        goal_id="G-1",
        artifact_id="A-1",
    )
    bridge = build_eclipse_sgpt_cli_operation_bridge(enabled=True, command_name="ananta-cli")

    assert review["schema"] == "eclipse_diff_review_render_v1"
    assert review["proposals"][0]["auto_apply"] is False
    assert approval["schema"] == "eclipse_review_approval_action_support_v1"
    assert approval["action_allowed"] is True
    assert shortcuts["schema"] == "eclipse_open_in_browser_shortcuts_v1"
    assert len(shortcuts["shortcuts"]) == 3
    assert bridge["schema"] == "eclipse_sgpt_cli_operation_bridge_v1"
    assert bridge["enabled"] is True
    assert bridge["secondary_path_only"] is True


def test_eclipse_extension_and_safety_contracts_cover_last_block() -> None:
    openai_eval = build_eclipse_openai_fallback_evaluation(
        endpoint_compatible=True,
        decision="keep_as_optional_fallback",
    )
    mcp_eval = build_eclipse_mcp_integration_evaluation(
        feasibility="medium",
        rest_preference_reason="REST path is thinner",
    )
    context_rules = build_eclipse_context_packaging_rules(max_selection_chars=3000, max_files=15)
    security = build_eclipse_security_privacy_guardrails()
    degraded = build_eclipse_error_degraded_mode(auth_failed=True, backend_reachable=False, policy_denied=False)
    trace = build_eclipse_trace_visibility(
        trace_id="trace-1",
        routing_summary="hub->worker",
        advanced_mode=True,
    )
    first_run = build_eclipse_first_run_ux(recommended_use_case="review")
    demo = build_eclipse_golden_path_demo()
    smoke = build_eclipse_manual_smoke_checklist()
    roadmap = build_eclipse_future_roadmap()

    assert openai_eval["schema"] == "eclipse_openai_fallback_evaluation_v1"
    assert openai_eval["must_not_replace_main_task_flows"] is True
    assert mcp_eval["schema"] == "eclipse_mcp_integration_evaluation_v1"
    assert context_rules["schema"] == "eclipse_context_packaging_rules_v1"
    assert context_rules["max_files"] == 15
    assert security["schema"] == "eclipse_security_privacy_guardrails_v1"
    assert security["redact_sensitive_logs"] is True
    assert degraded["schema"] == "eclipse_error_degraded_mode_v1"
    assert degraded["mode"] == "auth_failure"
    assert trace["schema"] == "eclipse_trace_visibility_v1"
    assert trace["visible_only_in_advanced_mode"] is True
    assert first_run["schema"] == "eclipse_first_run_ux_v1"
    assert first_run["minimal_manual_setup"] is True
    assert demo["schema"] == "eclipse_golden_path_demo_v1"
    assert smoke["schema"] == "eclipse_manual_smoke_checklist_v1"
    assert roadmap["schema"] == "eclipse_future_roadmap_v1"
    assert roadmap["mvp_scope_protected"] is True


def test_eclipse_views_extension_contracts_cover_first_ten_tasks() -> None:
    workspace = collect_eclipse_workspace_project_context(
        {"workspace_path": "/workspace", "project_name": "demo", "active_file_path": "/workspace/src/main.py"}
    )
    handoff = build_eclipse_selection_editor_handoff(
        {"file_path": "/workspace/src/main.py", "selection_text": "print('x')"}
    )
    strategy = build_eclipse_view_strategy()
    goal_view = build_eclipse_goal_quick_action_view(
        goal_text="Analyze this project",
        workspace_context=workspace,
    )
    task_list = build_eclipse_task_list_view(
        [{"id": "T-1", "title": "Task", "status": "todo", "review_required": True, "next_step": "inspect"}]
    )
    artifact_view = build_eclipse_artifact_view(
        [{"id": "A-1", "title": "Artifact", "type": "markdown", "task_id": "T-1"}]
    )
    context_view = build_eclipse_context_inspection_view(
        workspace_context=workspace,
        handoff_context=handoff,
    )
    detail_view = build_eclipse_basic_task_detail_view(
        {"id": "T-1", "title": "Task", "status": "todo", "summary": "Inspect detail"},
        artifacts=[{"id": "A-1", "title": "Artifact", "type": "markdown", "task_id": "T-1"}],
        routing_hints=["hub_queue_worker_path"],
    )
    review_view = build_eclipse_review_proposal_view(
        proposals=[{"id": "P-1", "title": "Proposal", "hunks": [{"path": "src/main.py", "line": 2}]}],
        approval_actions_supported=True,
    )
    blueprint_view = build_eclipse_blueprint_work_profile_view(
        [
            {
                "id": "bp-default",
                "purpose": "Default path",
                "recommended_goal_modes": ["analyze", "review"],
                "typical_outputs": ["task_plan"],
            }
        ]
    )
    runtime_view = build_eclipse_connection_runtime_status_view(
        profile={"id": "dev-local", "base_url": "http://localhost:8080", "auth_method": "session_token"},
        connected=True,
        health_state="ready",
        required_capabilities=["goals", "tasks"],
    )
    nav_model = build_eclipse_view_navigation_linking_model()

    assert strategy["schema"] == "eclipse_view_strategy_v1"
    assert strategy["does_not_replace_web_ui"] is True
    assert goal_view["schema"] == "eclipse_goal_quick_action_view_v1"
    assert "analyze" in goal_view["quick_actions"]
    assert task_list["schema"] == "eclipse_task_list_view_v1"
    assert task_list["detail_view_link_available"] is True
    assert artifact_view["schema"] == "eclipse_artifact_view_v1"
    assert artifact_view["open_in_browser_available"] is True
    assert context_view["schema"] == "eclipse_context_inspection_view_v1"
    assert context_view["can_remove_or_adjust_context"] is True
    assert detail_view["schema"] == "eclipse_basic_task_detail_view_v1"
    assert detail_view["not_an_ops_dashboard"] is True
    assert review_view["schema"] == "eclipse_review_proposal_view_v1"
    assert review_view["auditable_review_workflow"] is True
    assert blueprint_view["schema"] == "eclipse_blueprint_work_profile_view_v1"
    assert blueprint_view["supports_starting_path_selection"] is True
    assert runtime_view["schema"] == "eclipse_connection_runtime_status_view_v1"
    assert runtime_view["lightweight_not_full_admin_surface"] is True
    assert nav_model["schema"] == "eclipse_view_navigation_linking_model_v1"
    assert nav_model["preserves_task_context"] is True


def test_foundation_snapshot_covers_full_eclipse_track() -> None:
    snapshot = build_eclipse_plugin_adapter_foundation_snapshot(
        profile={"id": "dev-local", "base_url": "http://localhost:8080", "auth_method": "session_token"},
        workspace_state={
            "workspace_path": "/workspace",
            "project_name": "demo",
            "active_file_path": "/workspace/src/main.py",
            "selected_paths": ["/workspace/src/main.py"],
        },
        editor_state={
            "file_path": "/workspace/src/main.py",
            "selection_text": "print('hello')",
            "file_content_excerpt": "print('hello')",
        },
        goal_text="Analyze current module",
        tasks=[{"id": "T-1", "title": "Analyze task", "status": "todo", "next_step": "wait"}],
        artifacts=[{"id": "A-1", "title": "Analyze output", "type": "markdown", "task_id": "T-1"}],
        proposals=[{"id": "P-1", "title": "Patch proposal", "hunks": [{"path": "src/main.py", "line": 3}]}],
    )

    assert snapshot["schema"] == "eclipse_plugin_adapter_foundation_snapshot_v1"
    assert snapshot["plugin_target_model"]["schema"] == "eclipse_plugin_target_model_v1"
    assert snapshot["minimum_support_matrix"]["schema"] == "eclipse_minimum_support_matrix_v1"
    assert snapshot["connection_auth_support"]["schema"] == "eclipse_connection_auth_support_v1"
    assert snapshot["health_capability_handshake"]["schema"] == "eclipse_health_capability_handshake_v1"
    assert snapshot["workspace_project_context"]["schema"] == "eclipse_workspace_project_context_v1"
    assert snapshot["selection_editor_handoff"]["schema"] == "eclipse_selection_editor_handoff_v1"
    assert snapshot["core_command_set"]["schema"] == "eclipse_core_command_set_v1"
    assert snapshot["goal_input_panel"]["schema"] == "eclipse_goal_input_panel_v1"
    assert snapshot["task_artifact_view"]["schema"] == "eclipse_task_artifact_view_v1"
    assert snapshot["task_refresh_flow"]["schema"] == "eclipse_task_refresh_flow_v1"
    assert snapshot["diff_review_render"]["schema"] == "eclipse_diff_review_render_v1"
    assert snapshot["review_approval_action_support"]["schema"] == "eclipse_review_approval_action_support_v1"
    assert snapshot["open_in_browser_shortcuts"]["schema"] == "eclipse_open_in_browser_shortcuts_v1"
    assert snapshot["operation_presets"]["schema"] == "eclipse_operation_presets_v1"
    assert snapshot["sgpt_cli_operation_bridge"]["schema"] == "eclipse_sgpt_cli_operation_bridge_v1"
    assert snapshot["openai_fallback_evaluation"]["schema"] == "eclipse_openai_fallback_evaluation_v1"
    assert snapshot["mcp_integration_evaluation"]["schema"] == "eclipse_mcp_integration_evaluation_v1"
    assert snapshot["context_packaging_rules"]["schema"] == "eclipse_context_packaging_rules_v1"
    assert snapshot["security_privacy_guardrails"]["schema"] == "eclipse_security_privacy_guardrails_v1"
    assert snapshot["error_degraded_mode"]["schema"] == "eclipse_error_degraded_mode_v1"
    assert snapshot["trace_visibility"]["schema"] == "eclipse_trace_visibility_v1"
    assert snapshot["first_run_ux"]["schema"] == "eclipse_first_run_ux_v1"
    assert snapshot["golden_path_demo"]["schema"] == "eclipse_golden_path_demo_v1"
    assert snapshot["manual_smoke_checklist"]["schema"] == "eclipse_manual_smoke_checklist_v1"
    assert snapshot["future_roadmap"]["schema"] == "eclipse_future_roadmap_v1"
    assert snapshot["views_extension_snapshot"]["schema"] == "eclipse_views_extension_snapshot_v1"
    assert snapshot["views_extension_snapshot"]["view_strategy"]["schema"] == "eclipse_view_strategy_v1"
    assert (
        snapshot["views_extension_snapshot"]["goal_quick_action_view"]["schema"] == "eclipse_goal_quick_action_view_v1"
    )
    assert snapshot["views_extension_snapshot"]["task_list_view"]["schema"] == "eclipse_task_list_view_v1"
    assert snapshot["views_extension_snapshot"]["artifact_view"]["schema"] == "eclipse_artifact_view_v1"
    assert (
        snapshot["views_extension_snapshot"]["context_inspection_view"]["schema"]
        == "eclipse_context_inspection_view_v1"
    )
    assert (
        snapshot["views_extension_snapshot"]["basic_task_detail_view"]["schema"] == "eclipse_basic_task_detail_view_v1"
    )
    assert snapshot["views_extension_snapshot"]["review_proposal_view"]["schema"] == "eclipse_review_proposal_view_v1"
    assert (
        snapshot["views_extension_snapshot"]["blueprint_work_profile_view"]["schema"]
        == "eclipse_blueprint_work_profile_view_v1"
    )
    assert (
        snapshot["views_extension_snapshot"]["connection_runtime_status_view"]["schema"]
        == "eclipse_connection_runtime_status_view_v1"
    )
    assert (
        snapshot["views_extension_snapshot"]["view_navigation_linking_model"]["schema"]
        == "eclipse_view_navigation_linking_model_v1"
    )
