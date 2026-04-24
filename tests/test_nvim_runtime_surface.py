from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from scripts.smoke_nvim_runtime import run_smoke_once

ROOT = Path(__file__).resolve().parents[1]


def test_nvim_runtime_files_exist() -> None:
    required_files = [
        ROOT / "client_surfaces/nvim_runtime/plugin/ananta.vim",
        ROOT / "client_surfaces/nvim_runtime/lua/ananta/init.lua",
        ROOT / "client_surfaces/nvim_runtime/lua/ananta/client.lua",
        ROOT / "client_surfaces/nvim_runtime/lua/ananta/context.lua",
        ROOT / "client_surfaces/nvim_runtime/lua/ananta/render.lua",
        ROOT / "scripts/smoke_nvim_runtime.py",
    ]
    for file_path in required_files:
        assert file_path.exists(), f"missing required nvim runtime file: {file_path}"


def test_nvim_plugin_entrypoint_registers_core_commands() -> None:
    plugin_file = ROOT / "client_surfaces/nvim_runtime/plugin/ananta.vim"
    content = plugin_file.read_text(encoding="utf-8")
    for command in (
        "AnantaGoalSubmit",
        "AnantaAnalyze",
        "AnantaReview",
        "AnantaPatchPlan",
        "AnantaProjectNew",
        "AnantaProjectEvolve",
    ):
        assert command in content


def test_nvim_bridge_fixture_executes_command_path() -> None:
    env = os.environ.copy()
    env["ANANTA_NVIM_FIXTURE"] = "1"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "client_surfaces.nvim_runtime.ananta_bridge",
            "--command",
            "analyze",
            "--base-url",
            "http://localhost:8080",
            "--file-path",
            "/workspace/src/main.py",
            "--project-root",
            "/workspace",
            "--selection-text",
            "print('hello')",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["schema"] == "ananta_nvim_bridge_response_v1"
    assert payload["command"] == "analyze"
    assert payload["response"]["ok"] is True
    assert payload["response"]["state"] == "healthy"


def test_nvim_smoke_script_returns_success_or_explicit_skip() -> None:
    ok, output = run_smoke_once()
    assert ok is True
    assert "nvim-runtime-smoke-ok" in output or "nvim-runtime-smoke-skipped" in output
