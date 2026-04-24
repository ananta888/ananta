from __future__ import annotations

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
        "[TASKS]",
        "[ARTIFACTS]",
        "[KNOWLEDGE]",
        "[CONFIG]",
        "[PROVIDERS]",
        "[BENCHMARKS]",
        "[SYSTEM]",
        "[TEAMS]",
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
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "mode=compact current=Tasks" in result.stdout
    assert "selected_task=T-1" in result.stdout


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
