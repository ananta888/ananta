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
    assert "[HEALTH]" in result.stdout
    assert "[TASKS]" in result.stdout
    assert "[ARTIFACTS]" in result.stdout
    assert "[APPROVALS]" in result.stdout
    assert "[REPAIRS]" in result.stdout


def test_tui_runtime_main_returns_error_for_invalid_profile(capsys) -> None:
    rc = main(["--base-url", "localhost:8080"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "invalid_profile" in captured.out


def test_tui_runtime_app_shows_degraded_health_state() -> None:
    def transport(_method, _url, _headers, _body, _timeout):  # noqa: ANN001
        return 503, '{"error":"backend_down"}'

    client = AnantaApiClient(
        build_client_profile({"profile_id": "ops", "base_url": "http://localhost:8080"}),
        transport=transport,
    )
    rendered = TuiRuntimeApp(client).run_once()
    assert "state=backend_unreachable" in rendered


def test_smoke_tui_runtime_script_function_reports_success() -> None:
    ok, output = run_smoke_once()
    assert ok is True
    assert "[HEALTH]" in output
