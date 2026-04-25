from __future__ import annotations

import subprocess
import sys


def _run_tui(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "client_surfaces.tui_runtime.ananta_tui", *args],
        check=False,
        capture_output=True,
        text=True,
    )


def test_cli_goal_and_artifact_regression_flow_remains_explicit_and_safe() -> None:
    result = _run_tui(
        "--fixture",
        "--goal-create-text",
        "Regression goal",
        "--goal-create-mode",
        "guided",
        "--goal-create-context-json",
        '{"scope":"regression"}',
        "--selected-artifact-id",
        "A-1",
        "--artifact-action",
        "index",
        "--artifact-action-json",
        '{"profile":"default"}',
    )
    assert result.returncode == 0
    assert "[GOAL-CREATE] state=healthy" in result.stdout
    assert "[ARTIFACT-ACTION] preview_only action=index artifact_id=A-1" in result.stdout


def test_approval_regression_covers_allow_deny_and_stale_paths() -> None:
    approved = _run_tui("--fixture", "--selected-task-id", "T-1", "--approval-action", "approve", "--confirm-approval-action")
    stale = _run_tui("--fixture", "--selected-task-id", "T-2", "--approval-action", "reject", "--confirm-approval-action")
    denied = _run_tui("--fixture", "--selected-task-id", "T-3", "--approval-action", "approve", "--confirm-approval-action")

    assert approved.returncode == 0
    assert stale.returncode == 0
    assert denied.returncode == 0
    assert "[APPROVAL-ACTION] applied action=approve task_id=T-1 state=healthy" in approved.stdout
    assert "[APPROVAL-ACTION] skipped=stale task_id=T-2" in stale.stdout
    assert "[APPROVAL-ACTION] applied action=approve task_id=T-3 state=policy_denied" in denied.stdout


def test_repair_regression_blocks_unsafe_and_enforces_browser_first_execution() -> None:
    unsafe = _run_tui(
        "--fixture",
        "--selected-repair-session-id",
        "R-1",
        "--repair-action",
        "execute",
        "--repair-action-json",
        '{"unsafe": true}',
        "--confirm-repair-action",
    )
    guarded = _run_tui(
        "--fixture",
        "--selected-repair-session-id",
        "R-1",
        "--repair-action",
        "execute",
        "--confirm-repair-action",
    )

    assert unsafe.returncode == 0
    assert guarded.returncode == 0
    assert "[REPAIR-ACTION] blocked=unsafe_payload session_id=R-1" in unsafe.stdout
    assert "[REPAIR-ACTION] blocked=browser_fallback_required action=execute session_id=R-1" in guarded.stdout


def test_degraded_backend_behavior_is_visible_and_not_reported_as_success() -> None:
    result = _run_tui("--base-url", "http://127.0.0.1:1")

    assert result.returncode == 0
    assert "health_state=backend_unreachable" in result.stdout
    assert "dashboard_degraded=" in result.stdout
