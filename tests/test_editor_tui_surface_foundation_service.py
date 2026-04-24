from agent.services.editor_tui_surface_foundation_service import (
    build_connection_profile_abstraction,
    build_editor_tui_foundation_snapshot,
    build_nvim_connection_auth_support,
    build_nvim_goal_submission_flow,
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


def test_build_nvim_goal_submission_flow_includes_context_and_use_case_alignment() -> None:
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
    flow = build_nvim_goal_submission_flow(goal_text="Review this function", editor_context=context)

    assert flow["schema"] == "nvim_goal_submission_flow_v1"
    assert flow["goal_text"] == "Review this function"
    assert "review" in flow["use_case_alignment"]
    assert flow["context_payload"]["file_path"] == "/workspace/src/main.py"
    assert flow["context_payload"]["selection"]["present"] is True
    assert flow["explicit_user_trigger_required"] is True


def test_build_editor_tui_foundation_snapshot_contains_first_15_task_contracts() -> None:
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
    assert snapshot["nvim_editor_context"]["schema"] == "nvim_editor_context_v1"
    assert snapshot["nvim_goal_submission_flow"]["schema"] == "nvim_goal_submission_flow_v1"
    command_names = [item["command"] for item in snapshot["nvim_command_surface"]["commands"]]
    assert "AnantaAnalyze" in command_names
    assert "AnantaGoalSubmit" in command_names
