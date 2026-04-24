from agent.services.eclipse_plugin_adapter_foundation_service import (
    build_eclipse_connection_auth_support,
    build_eclipse_diff_review_render,
    build_eclipse_goal_input_panel,
    build_eclipse_health_capability_handshake,
    build_eclipse_minimum_support_matrix,
    build_eclipse_open_in_browser_shortcuts,
    build_eclipse_plugin_adapter_foundation_snapshot,
    build_eclipse_review_approval_action_support,
    build_eclipse_selection_editor_handoff,
    build_eclipse_sgpt_cli_operation_bridge,
    build_eclipse_task_artifact_view,
    build_eclipse_task_refresh_flow,
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


def test_foundation_snapshot_covers_first_fifteen_eclipse_tasks() -> None:
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
