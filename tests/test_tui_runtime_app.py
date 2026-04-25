from __future__ import annotations

import json
import subprocess
import sys

from client_surfaces.common.client_api import AnantaApiClient
from client_surfaces.common.profile_auth import build_client_profile
from client_surfaces.tui_runtime.ananta_tui.app import TuiRuntimeApp, main
from scripts.smoke_tui_runtime import run_smoke_once


def test_tui_runtime_app_renders_fixture_sections() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "client_surfaces.tui_runtime.ananta_tui", "--fixture"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    for marker in (
        "[NAVIGATION]",
        "[API-MAP]",
        "[DASHBOARD]",
        "[GOALS]",
        "[GOAL-DETAIL]",
        "[GOAL-PLAN]",
        "[TASK-WORKBENCH]",
        "[TASK-ORCHESTRATION]",
        "[ARCHIVED-TASKS]",
        "[ARTIFACT-EXPLORER]",
        "[KNOWLEDGE]",
        "[TEMPLATES]",
        "[CONFIG]",
        "[PROVIDERS]",
        "[BENCHMARKS]",
        "[SYSTEM]",
        "[TEAMS]",
        "[BLUEPRINTS]",
        "[INSTRUCTION-LAYERS]",
        "[INSTRUCTION-EFFECTIVE]",
        "[INSTRUCTION-PROFILES]",
        "[INSTRUCTION-OVERLAYS]",
        "[AUTOMATION]",
        "[AUDIT]",
        "[APPROVALS]",
        "[REPAIRS]",
        "[HELP]",
    ):
        assert marker in result.stdout


def test_tui_runtime_main_returns_error_for_invalid_profile(capsys) -> None:
    rc = main(["--base-url", "localhost:8080"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "invalid_profile" in captured.out


def test_tui_runtime_compact_navigation_and_selected_context() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "client_surfaces.tui_runtime.ananta_tui",
            "--fixture",
            "--section",
            "Tasks",
            "--terminal-width",
            "70",
            "--selected-task-id",
            "T-1",
            "--selected-collection-id",
            "KC-1",
            "--selected-template-id",
            "TPL-1",
            "--selected-team-id",
            "team-core",
            "--selected-blueprint-id",
            "BP-1",
            "--selected-instruction-profile-id",
            "IP-1",
            "--selected-instruction-overlay-id",
            "IO-1",
            "--selected-approval-id",
            "AP-1",
            "--selected-repair-session-id",
            "R-1",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "mode=compact current=Tasks" in result.stdout
    assert "selected_task=T-1" in result.stdout
    assert "selected_collection=KC-1" in result.stdout
    assert "selected_template=TPL-1" in result.stdout
    assert "selected_team=team-core" in result.stdout
    assert "selected_blueprint=BP-1" in result.stdout
    assert "selected_profile=IP-1" in result.stdout
    assert "selected_overlay=IO-1" in result.stdout
    assert "selected_approval=AP-1" in result.stdout
    assert "selected_repair=R-1" in result.stdout


def test_tui_runtime_safe_config_edit_preview_and_apply() -> None:
    preview = subprocess.run(
        [
            sys.executable,
            "-m",
            "client_surfaces.tui_runtime.ananta_tui",
            "--fixture",
            "--set-safe-config",
            "runtime_profile=strict",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert preview.returncode == 0
    assert "[CONFIG-EDIT] preview_only" in preview.stdout

    applied = subprocess.run(
        [
            sys.executable,
            "-m",
            "client_surfaces.tui_runtime.ananta_tui",
            "--fixture",
            "--set-safe-config",
            "runtime_profile=strict",
            "--apply-safe-config",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert applied.returncode == 0
    assert "[CONFIG-EDIT] applied" in applied.stdout


def test_tui_runtime_goal_task_artifact_actions_are_guarded() -> None:
    preview = subprocess.run(
        [
            sys.executable,
            "-m",
            "client_surfaces.tui_runtime.ananta_tui",
            "--fixture",
            "--goal-create-text",
            "Add runtime checks",
            "--goal-create-mode",
            "guided",
            "--goal-create-context-json",
            '{"scope":"tui"}',
            "--selected-task-id",
            "T-1",
            "--task-action",
            "patch",
            "--task-action-json",
            '{"status":"in_progress"}',
            "--selected-artifact-id",
            "A-1",
            "--artifact-action",
            "index",
            "--artifact-action-json",
            '{"profile":"default"}',
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert preview.returncode == 0
    assert "[GOAL-CREATE] state=healthy" in preview.stdout
    assert "[TASK-ACTION] preview_only" in preview.stdout
    assert "[ARTIFACT-ACTION] preview_only" in preview.stdout

    apply_actions = subprocess.run(
        [
            sys.executable,
            "-m",
            "client_surfaces.tui_runtime.ananta_tui",
            "--fixture",
            "--selected-task-id",
            "T-1",
            "--task-action",
            "execute",
            "--task-action-json",
            '{"dry_run":true}',
            "--confirm-task-action",
            "--selected-artifact-id",
            "A-1",
            "--artifact-action",
            "extract",
            "--confirm-artifact-action",
            "--selected-archived-task-id",
            "TA-1",
            "--archived-action",
            "restore",
            "--confirm-archived-action",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert apply_actions.returncode == 0
    assert "[TASK-ACTION] applied action=execute state=healthy" in apply_actions.stdout
    assert "[ARTIFACT-ACTION] applied action=extract state=healthy" in apply_actions.stdout
    assert "[ARCHIVED-ACTION] applied action=restore state=healthy" in apply_actions.stdout


def test_tui_runtime_knowledge_and_template_operations() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "client_surfaces.tui_runtime.ananta_tui",
            "--fixture",
            "--selected-collection-id",
            "KC-1",
            "--knowledge-search-query",
            "parity",
            "--knowledge-top-k",
            "3",
            "--index-selected-collection",
            "--confirm-knowledge-index",
            "--template-operation",
            "diagnostics",
            "--template-payload-json",
            '{"template":"x"}',
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "[KNOWLEDGE-ACTION] index_state=healthy search_state=healthy search_hits=1" in result.stdout
    assert "[TEMPLATE-OP] operation=diagnostics state=healthy" in result.stdout


def test_tui_runtime_team_instruction_automation_actions() -> None:
    preview = subprocess.run(
        [
            sys.executable,
            "-m",
            "client_surfaces.tui_runtime.ananta_tui",
            "--fixture",
            "--selected-team-id",
            "team-core",
            "--team-action",
            "activate",
            "--selected-instruction-profile-id",
            "IP-1",
            "--instruction-action",
            "select_profile",
            "--automation-action",
            "autopilot_start",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert preview.returncode == 0
    assert "[TEAM-ACTION] preview_only action=activate team_id=team-core" in preview.stdout
    assert "[INSTRUCTION-ACTION] preview_only action=select_profile" in preview.stdout
    assert "[AUTOMATION-ACTION] preview_only action=autopilot_start" in preview.stdout

    apply_actions = subprocess.run(
        [
            sys.executable,
            "-m",
            "client_surfaces.tui_runtime.ananta_tui",
            "--fixture",
            "--selected-team-id",
            "team-core",
            "--team-action",
            "activate",
            "--confirm-team-action",
            "--selected-instruction-overlay-id",
            "IO-1",
            "--instruction-action",
            "select_overlay",
            "--instruction-action-json",
            '{"reason":"operator_override"}',
            "--confirm-instruction-action",
            "--automation-action",
            "autopilot_tick",
            "--confirm-automation-action",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert apply_actions.returncode == 0
    assert "[TEAM-ACTION] applied action=activate team_id=team-core state=healthy" in apply_actions.stdout
    assert "[INSTRUCTION-ACTION] applied action=select_overlay state=healthy" in apply_actions.stdout
    assert "[AUTOMATION-ACTION] applied action=autopilot_tick state=healthy" in apply_actions.stdout


def test_tui_runtime_approval_and_repair_actions_are_guarded() -> None:
    preview = subprocess.run(
        [
            sys.executable,
            "-m",
            "client_surfaces.tui_runtime.ananta_tui",
            "--fixture",
            "--selected-task-id",
            "T-1",
            "--approval-action",
            "approve",
            "--approval-action-json",
            '{"comment":"looks good"}',
            "--selected-repair-session-id",
            "R-1",
            "--repair-action",
            "execute",
            "--repair-action-json",
            '{"unsafe": false}',
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert preview.returncode == 0
    assert "[APPROVAL-ACTION] preview_only action=approve task_id=T-1" in preview.stdout
    assert "[REPAIR-ACTION] preview_only action=execute session_id=R-1" in preview.stdout

    apply_actions = subprocess.run(
        [
            sys.executable,
            "-m",
            "client_surfaces.tui_runtime.ananta_tui",
            "--fixture",
            "--selected-task-id",
            "T-1",
            "--approval-action",
            "approve",
            "--confirm-approval-action",
            "--selected-repair-session-id",
            "R-1",
            "--repair-action",
            "execute",
            "--confirm-repair-action",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert apply_actions.returncode == 0
    assert "[APPROVAL-ACTION] applied action=approve task_id=T-1 state=healthy" in apply_actions.stdout
    assert "[REPAIR-ACTION] blocked=browser_fallback_required action=execute session_id=R-1" in apply_actions.stdout

    stale = subprocess.run(
        [
            sys.executable,
            "-m",
            "client_surfaces.tui_runtime.ananta_tui",
            "--fixture",
            "--selected-task-id",
            "T-2",
            "--approval-action",
            "reject",
            "--confirm-approval-action",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert stale.returncode == 0
    assert "[APPROVAL-ACTION] skipped=stale task_id=T-2" in stale.stdout

    denied = subprocess.run(
        [
            sys.executable,
            "-m",
            "client_surfaces.tui_runtime.ananta_tui",
            "--fixture",
            "--selected-task-id",
            "T-3",
            "--approval-action",
            "approve",
            "--confirm-approval-action",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert denied.returncode == 0
    assert "[APPROVAL-ACTION] applied action=approve task_id=T-3 state=policy_denied" in denied.stdout


def test_tui_runtime_live_refresh_block_is_rendered() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "client_surfaces.tui_runtime.ananta_tui",
            "--fixture",
            "--selected-task-id",
            "T-1",
            "--live-refresh-target",
            "system_task_logs",
            "--live-refresh-cycles",
            "2",
            "--live-refresh-interval-seconds",
            "0.2",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "[LIVE-REFRESH]" in result.stdout
    assert "cycle=1/2 health=healthy" in result.stdout
    assert "cycle=2/2 health=healthy" in result.stdout
    assert "task_logs_state=healthy task_id=T-1" in result.stdout


def test_tui_runtime_app_shows_degraded_health_state() -> None:
    def transport(_method, _url, _headers, _body, _timeout):  # noqa: ANN001
        return 503, '{"error":"backend_down"}'

    client = AnantaApiClient(
        build_client_profile({"profile_id": "ops", "base_url": "http://localhost:8080"}),
        transport=transport,
    )
    rendered = TuiRuntimeApp(client).run_once()
    assert "health_state=backend_unreachable" in rendered
    assert "dashboard_degraded=" in rendered


def test_smoke_tui_runtime_script_function_reports_success() -> None:
    ok, output = run_smoke_once()
    assert ok is True
    assert "[NAVIGATION]" in output
    assert "[DASHBOARD]" in output
    assert "[HEALTH]" in output
    assert "[TASK-WORKBENCH]" in output
    assert "[ARTIFACT-EXPLORER]" in output


def test_tui_runtime_import_has_no_render_side_effects() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import client_surfaces.tui_runtime.ananta_tui"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""
    assert result.stderr.strip() == ""


def test_tui_runtime_fixture_startup_supports_structured_json_output() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "client_surfaces.tui_runtime.ananta_tui", "--fixture", "--json"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["schema"] == "ananta_tui_runtime_output_v3"
    assert "[DASHBOARD]" in payload["output"]
    assert "[TASK-WORKBENCH]" in payload["output"]


def test_tui_runtime_approval_action_handles_malformed_and_missing_target_requests() -> None:
    malformed = subprocess.run(
        [
            sys.executable,
            "-m",
            "client_surfaces.tui_runtime.ananta_tui",
            "--fixture",
            "--approval-action",
            "approve",
            "--approval-action-json",
            "{bad json",
            "--confirm-approval-action",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert malformed.returncode == 0
    assert "[APPROVAL-ACTION] rejected=json_parse_error:" in malformed.stdout

    missing_target = subprocess.run(
        [
            sys.executable,
            "-m",
            "client_surfaces.tui_runtime.ananta_tui",
            "--fixture",
            "--approval-action",
            "approve",
            "--confirm-approval-action",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert missing_target.returncode == 0
    assert "[APPROVAL-ACTION] rejected=selected_task_required" in missing_target.stdout


def test_tui_runtime_repair_action_blocks_unsafe_payloads() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "client_surfaces.tui_runtime.ananta_tui",
            "--fixture",
            "--selected-repair-session-id",
            "R-1",
            "--repair-action",
            "execute",
            "--repair-action-json",
            '{"unsafe": true}',
            "--confirm-repair-action",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "[REPAIR-ACTION] blocked=unsafe_payload session_id=R-1" in result.stdout
