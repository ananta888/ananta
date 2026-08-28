"""CLA-001/CLA-002/COMMON-002/COMMON-003/COMMON-005: Claude Code CLI
worker-agent adapter.

Acceptance criteria covered (todo.subscription-cli-adapters-codex-claude):

* CLA-001: cli_backend=claude_code runtime-registered (contract,
  semaphore, SUPPORTED_CLI_BACKENDS, executor_kind sets); adapter runs
  a read-only analysis task with stdout/stderr/exit_code; supports
  command, auth_mode, default_model, permission_mode, timeout and
  allowed_paths; ANTHROPIC_API_KEY not required for claude_login.
* CLA-002: preflight surfaces installed/auth_mode/login_command
  without reading token files; not_installed yields install hint.
* COMMON-001: args-list invocation, no shell=True (regression guard).
* COMMON-002: claude_code only auto-routed when enabled + installed.
* COMMON-003: per-backend health/diagnose endpoints.
* COMMON-005: all tests run with mock binaries, no real accounts,
  no ANTHROPIC_API_KEY needed.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# shared mock helpers (same style as tests/test_codex_cli_auth_mode.py)
# ---------------------------------------------------------------------------

def _fake_settings(
    *,
    claude_path: str = "claude",
    claude_auth_mode: str = "claude_login",
    claude_permission_mode: str = "plan",
    anthropic_api_key: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        claude_path=claude_path,
        claude_default_model="claude-code-default",
        claude_auth_mode=claude_auth_mode,
        claude_permission_mode=claude_permission_mode,
        claude_timeout_seconds=1800,
        claude_max_concurrent_runs=1,
        anthropic_api_key=anthropic_api_key,
        codex_path="codex",
        codex_default_model="gpt-5-codex",
        codex_auth_mode="api_key",
        codex_require_api_key=True,
        openai_api_key=None,
        openai_url=None,
        ollama_url=None,
        lmstudio_url=None,
        anthropic_url=None,
        mock_url=None,
        default_provider="lmstudio",
        max_prompt_tokens=128000,
        aider_path="aider",
        mistral_code_path="mistral-code",
        opencode_path="opencode",
        sgpt_path="sgpt",
        ananta_worker_path="ananta-worker",
        sgpt_execution_backend="ananta-worker",
        opencode_default_model="opencode/big-pickle",
        aider_default_model="aider-default",
        mistral_code_default_model="mistral-default",
    )


def _fake_app(agent_cfg: dict, *, provider_urls: dict | None = None) -> Any:
    from flask import Flask

    app = Flask(__name__)
    app.config["AGENT_CONFIG"] = agent_cfg
    app.config["PROVIDER_URLS"] = provider_urls or {}
    app.config["TESTING"] = True
    return app


def _completed(rc: int = 0, stdout: str = "OK", stderr: str = "") -> MagicMock:
    proc = MagicMock()
    proc.returncode = rc
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


# ---------------------------------------------------------------------------
# CLA-001 / resolve_claude_runtime_config
# ---------------------------------------------------------------------------

def test_resolve_claude_runtime_config_defaults_are_safe():
    """Default: disabled, claude_login, no api_key requirement,
    read-only permission_mode=plan."""
    from agent.cli_backends.opencode import resolve_claude_runtime_config

    app = _fake_app({})
    settings = _fake_settings()
    with app.app_context(), patch("agent.cli_backends.opencode.settings", settings):
        res = resolve_claude_runtime_config()
    assert res["enabled"] is False
    assert res["auth_mode"] == "claude_login"
    assert res["api_key_required"] is False
    assert res["permission_mode"] == "plan"
    assert "claude_cli_disabled" in res["diagnostics"]


def test_resolve_claude_runtime_config_api_key_mode_requires_key():
    from agent.cli_backends.opencode import resolve_claude_runtime_config

    app = _fake_app({"claude_cli": {"enabled": True, "auth_mode": "api_key"}})
    settings = _fake_settings()
    with app.app_context(), patch("agent.cli_backends.opencode.settings", settings), \
         patch.dict("os.environ", {}, clear=False):
        import os

        os.environ.pop("ANTHROPIC_API_KEY", None)
        res = resolve_claude_runtime_config()
    assert res["auth_mode"] == "api_key"
    assert res["api_key_required"] is True
    assert "claude_runtime_missing_api_key" in res["diagnostics"]


def test_resolve_claude_runtime_config_unknown_auth_mode_falls_back_to_claude_login():
    from agent.cli_backends.opencode import resolve_claude_runtime_config

    app = _fake_app({"claude_cli": {"enabled": True, "auth_mode": "oauth-magic"}})
    settings = _fake_settings()
    with app.app_context(), patch("agent.cli_backends.opencode.settings", settings):
        res = resolve_claude_runtime_config()
    assert res["auth_mode"] == "claude_login"
    assert res["api_key_required"] is False


def test_resolve_claude_runtime_config_rejects_bypass_permissions():
    """Fail-safe: bypassPermissions is never accepted; falls back to plan."""
    from agent.cli_backends.opencode import resolve_claude_runtime_config

    app = _fake_app({"claude_cli": {"enabled": True, "permission_mode": "bypassPermissions"}})
    settings = _fake_settings()
    with app.app_context(), patch("agent.cli_backends.opencode.settings", settings):
        res = resolve_claude_runtime_config()
    assert res["permission_mode"] == "plan"


@pytest.mark.parametrize(
    "permission_mode",
    ["plan", "manual", "acceptEdits", "dontAsk", "auto"],
)
def test_resolve_claude_runtime_config_accepts_supported_permission_modes(
    permission_mode,
):
    from agent.cli_backends.opencode import resolve_claude_runtime_config

    app = _fake_app(
        {"claude_cli": {"enabled": True, "permission_mode": permission_mode}}
    )
    settings = _fake_settings()
    with app.app_context(), patch(
        "agent.cli_backends.opencode.settings", settings
    ):
        result = resolve_claude_runtime_config()

    assert result["permission_mode"] == permission_mode


def test_resolve_claude_runtime_config_maps_legacy_default_to_manual():
    from agent.cli_backends.opencode import resolve_claude_runtime_config

    app = _fake_app(
        {"claude_cli": {"enabled": True, "permission_mode": "default"}}
    )
    settings = _fake_settings()
    with app.app_context(), patch(
        "agent.cli_backends.opencode.settings", settings
    ):
        result = resolve_claude_runtime_config()

    assert result["permission_mode"] == "manual"


def test_resolve_claude_runtime_config_bounds_timeout_and_concurrency():
    from agent.cli_backends.opencode import resolve_claude_runtime_config

    app = _fake_app({"claude_cli": {"enabled": True, "timeout_seconds": 999999, "max_concurrent_runs": 99}})
    settings = _fake_settings()
    with app.app_context(), patch("agent.cli_backends.opencode.settings", settings):
        res = resolve_claude_runtime_config()
    assert res["timeout_seconds"] == 14400
    assert res["max_concurrent_runs"] == 8


# ---------------------------------------------------------------------------
# CLA-001 / run_claude_command
# ---------------------------------------------------------------------------

def test_run_claude_command_disabled_returns_clear_error():
    from agent.cli_backends.opencode import run_claude_command

    app = _fake_app({"claude_cli": {"enabled": False}})
    settings = _fake_settings()
    with app.app_context(), patch("agent.cli_backends.opencode.settings", settings):
        rc, out, err = run_claude_command("analyse this")
    assert rc == -1
    assert "claude_cli.enabled" in err


def test_run_claude_command_not_installed_returns_install_hint():
    from agent.cli_backends.opencode import run_claude_command

    app = _fake_app({"claude_cli": {"enabled": True}})
    settings = _fake_settings()
    with app.app_context(), patch("agent.cli_backends.opencode.settings", settings), \
         patch("agent.cli_backends.opencode.shutil.which", return_value=None):
        rc, out, err = run_claude_command("analyse this")
    assert rc == -1
    assert "@anthropic-ai/claude-code" in err


def test_run_claude_command_claude_login_strips_anthropic_api_key_from_env():
    """CLA-001 acceptance: ANTHROPIC_API_KEY is not required and not
    injected in claude_login mode — an inherited env key is removed so
    the CLI uses its own local login session."""
    from agent.cli_backends.opencode import run_claude_command

    app = _fake_app({"claude_cli": {"enabled": True, "auth_mode": "claude_login"}})
    settings = _fake_settings()
    captured: dict[str, Any] = {}

    def _capture(args, **kwargs):
        captured["args"] = args
        captured["env"] = kwargs.get("env")
        captured["shell"] = kwargs.get("shell", False)
        return _completed()

    with app.app_context(), patch("agent.cli_backends.opencode.settings", settings), \
         patch("agent.cli_backends.opencode.shutil.which", return_value="/usr/bin/claude"), \
         patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-should-not-leak"}, clear=False), \
         patch("agent.cli_backends.opencode.subprocess.run", side_effect=_capture):
        rc, out, err = run_claude_command("analyse this", timeout=10)
    assert rc == 0
    assert "ANTHROPIC_API_KEY" not in captured["env"]
    assert captured["shell"] is False
    assert isinstance(captured["args"], list)
    assert captured["args"][0] == "/usr/bin/claude"


def test_run_claude_command_api_key_mode_injects_settings_key():
    from agent.cli_backends.opencode import run_claude_command

    app = _fake_app({"claude_cli": {"enabled": True, "auth_mode": "api_key"}})
    settings = _fake_settings(anthropic_api_key="sk-from-settings")
    captured: dict[str, Any] = {}

    def _capture(args, **kwargs):
        captured["env"] = kwargs.get("env")
        return _completed()

    with app.app_context(), patch("agent.cli_backends.opencode.settings", settings), \
         patch("agent.cli_backends.opencode.shutil.which", return_value="/usr/bin/claude"), \
         patch.dict("os.environ", {}, clear=False), \
         patch("agent.cli_backends.opencode.subprocess.run", side_effect=_capture):
        import os

        os.environ.pop("ANTHROPIC_API_KEY", None)
        rc, out, err = run_claude_command("analyse this", timeout=10)
    assert rc == 0
    assert captured["env"].get("ANTHROPIC_API_KEY") == "sk-from-settings"


def test_run_claude_command_default_model_sentinel_skips_model_flag():
    from agent.cli_backends.opencode import run_claude_command

    app = _fake_app({"claude_cli": {"enabled": True}})
    settings = _fake_settings()
    captured: dict[str, Any] = {}

    def _capture(args, **kwargs):
        captured["args"] = args
        return _completed()

    with app.app_context(), patch("agent.cli_backends.opencode.settings", settings), \
         patch("agent.cli_backends.opencode.shutil.which", return_value="/usr/bin/claude"), \
         patch("agent.cli_backends.opencode.subprocess.run", side_effect=_capture):
        run_claude_command("analyse this", timeout=10)
    assert "--model" not in captured["args"]
    assert "--permission-mode" in captured["args"]
    assert "plan" in captured["args"]


def test_run_claude_command_explicit_model_is_passed():
    from agent.cli_backends.opencode import run_claude_command

    app = _fake_app({"claude_cli": {"enabled": True}})
    settings = _fake_settings()
    captured: dict[str, Any] = {}

    def _capture(args, **kwargs):
        captured["args"] = args
        return _completed()

    with app.app_context(), patch("agent.cli_backends.opencode.settings", settings), \
         patch("agent.cli_backends.opencode.shutil.which", return_value="/usr/bin/claude"), \
         patch("agent.cli_backends.opencode.subprocess.run", side_effect=_capture):
        run_claude_command("analyse this", model="claude-sonnet-5", timeout=10)
    idx = captured["args"].index("--model")
    assert captured["args"][idx + 1] == "claude-sonnet-5"


def test_run_claude_command_timeout_maps_to_clean_error():
    import subprocess as real_subprocess

    from agent.cli_backends.opencode import run_claude_command

    app = _fake_app({"claude_cli": {"enabled": True}})
    settings = _fake_settings()
    with app.app_context(), patch("agent.cli_backends.opencode.settings", settings), \
         patch("agent.cli_backends.opencode.shutil.which", return_value="/usr/bin/claude"), \
         patch(
             "agent.cli_backends.opencode.subprocess.run",
             side_effect=real_subprocess.TimeoutExpired(cmd="claude", timeout=10),
         ):
        rc, out, err = run_claude_command("analyse this", timeout=10)
    assert rc == -1
    assert err == "Timeout"


def test_run_claude_command_workdir_outside_allowed_paths_is_rejected(tmp_path):
    from agent.cli_backends.opencode import run_claude_command

    allowed = tmp_path / "workspace"
    allowed.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    app = _fake_app({"claude_cli": {"enabled": True, "allowed_paths": [str(allowed)]}})
    settings = _fake_settings()
    with app.app_context(), patch("agent.cli_backends.opencode.settings", settings), \
         patch("agent.cli_backends.opencode.shutil.which", return_value="/usr/bin/claude"):
        rc, out, err = run_claude_command("analyse this", timeout=10, workdir=str(outside))
    assert rc == -1
    assert "allowed_paths" in err


def test_run_claude_command_source_has_no_shell_true():
    """COMMON-001 regression guard: the claude adapter never uses
    shell=True."""
    import inspect

    from agent.cli_backends import opencode as opencode_mod

    source = inspect.getsource(opencode_mod.run_claude_command)
    assert "shell=True" not in source


# ---------------------------------------------------------------------------
# CLA-002 / preflight + login command
# ---------------------------------------------------------------------------

def test_claude_login_command_for_mode():
    from agent.cli_backends.routing import _claude_login_command_for_mode

    settings = _fake_settings()
    with patch("agent.cli_backends.routing.settings", settings):
        assert _claude_login_command_for_mode("claude_login") == "claude login"
        assert _claude_login_command_for_mode("api_key") is None
        assert _claude_login_command_for_mode(None) is None


def test_preflight_contains_claude_provider_block():
    from agent.cli_backends.routing import get_cli_backend_preflight

    app = _fake_app({"claude_cli": {"enabled": True, "auth_mode": "claude_login"}})
    settings = _fake_settings()
    with app.app_context(), \
         patch("agent.cli_backends.routing.settings", settings), \
         patch("agent.cli_backends.opencode.settings", settings), \
         patch("agent.cli_backends.routing.shutil.which", return_value="/usr/bin/claude"):
        preflight = get_cli_backend_preflight(runtime_scope="worker")
    claude = preflight["providers"]["claude"]
    assert claude["enabled"] is True
    assert claude["installed"] is True
    assert claude["auth_mode"] == "claude_login"
    assert claude["api_key_required"] is False
    assert claude["login_command"] == "claude login"
    assert claude["install_hint"] == "npm i -g @anthropic-ai/claude-code"


def test_preflight_claude_not_installed_shows_hint_without_error():
    """CLA-002: not_installed is a diagnosable state, not an exception."""
    from agent.cli_backends.routing import get_cli_backend_preflight

    app = _fake_app({})
    settings = _fake_settings()
    with app.app_context(), \
         patch("agent.cli_backends.routing.settings", settings), \
         patch("agent.cli_backends.opencode.settings", settings), \
         patch("agent.cli_backends.routing.shutil.which", return_value=None):
        preflight = get_cli_backend_preflight(runtime_scope="worker")
    claude = preflight["providers"]["claude"]
    assert claude["enabled"] is False
    assert claude["installed"] is False
    assert claude["install_hint"] == "npm i -g @anthropic-ai/claude-code"
    backend_entry = preflight["cli_backends"]["claude_code"]
    assert backend_entry["binary_available"] is False
    assert backend_entry["verify_command"] == "claude --version"


def test_preflight_claude_api_key_mode_has_no_login_command():
    from agent.cli_backends.routing import get_cli_backend_preflight

    app = _fake_app({"claude_cli": {"enabled": True, "auth_mode": "api_key"}})
    settings = _fake_settings(anthropic_api_key="sk-x")
    with app.app_context(), \
         patch("agent.cli_backends.routing.settings", settings), \
         patch("agent.cli_backends.opencode.settings", settings), \
         patch("agent.cli_backends.routing.shutil.which", return_value="/usr/bin/claude"):
        preflight = get_cli_backend_preflight(runtime_scope="worker")
    claude = preflight["providers"]["claude"]
    assert claude["auth_mode"] == "api_key"
    assert claude["api_key_required"] is True
    assert claude["login_command"] is None


# ---------------------------------------------------------------------------
# COMMON-002 / routing gate
# ---------------------------------------------------------------------------

def test_choose_candidates_auto_excludes_disabled_claude():
    from agent.cli_backends.routing import _choose_candidates

    app = _fake_app({})
    settings = _fake_settings()
    with app.app_context(), \
         patch("agent.cli_backends.routing.settings", settings), \
         patch("agent.cli_backends.opencode.settings", settings):
        candidates = _choose_candidates(requested="auto", prompt="hello")
    assert "claude_code" not in candidates


def test_choose_candidates_auto_excludes_enabled_installed_claude_without_paid_fallback():
    from agent.cli_backends.routing import _choose_candidates

    app = _fake_app({"claude_cli": {"enabled": True}})
    settings = _fake_settings()
    with app.app_context(), \
         patch("agent.cli_backends.routing.settings", settings), \
         patch("agent.cli_backends.opencode.settings", settings), \
         patch("agent.cli_backends.routing.shutil.which", return_value="/usr/bin/claude"):
        candidates = _choose_candidates(requested="auto", prompt="hello")
    assert "claude_code" not in candidates


def test_choose_candidates_auto_includes_enabled_installed_claude_when_paid_fallback_allowed():
    from agent.cli_backends.routing import _choose_candidates

    app = _fake_app({"claude_cli": {"enabled": True}})
    settings = _fake_settings()
    with app.app_context(), \
         patch("agent.cli_backends.routing.settings", settings), \
         patch("agent.cli_backends.opencode.settings", settings), \
         patch("agent.cli_backends.routing.shutil.which", return_value="/usr/bin/claude"):
        candidates = _choose_candidates(
            requested="auto",
            prompt="hello",
            routing_policy={"allow_paid_coding_agent_fallback": True},
        )
    assert "claude_code" in candidates


def test_choose_candidates_explicit_claude_request_is_kept():
    """Explicit requests stay routable — run_claude_command reports the
    disabled/not_installed diagnosis itself."""
    from agent.cli_backends.routing import _choose_candidates

    app = _fake_app({})
    settings = _fake_settings()
    with app.app_context(), patch("agent.cli_backends.routing.settings", settings):
        candidates = _choose_candidates(requested="claude_code", prompt="hello")
    assert candidates == ["claude_code"]


def test_worker_selection_policy_treats_codex_and_claude_as_expensive():
    from agent.services.worker_selection_policy_service import _is_expensive_worker_name

    assert _is_expensive_worker_name("claude_code") is True
    assert _is_expensive_worker_name("codex-worker-1") is True
    assert _is_expensive_worker_name("ananta-worker") is False


# ---------------------------------------------------------------------------
# CLA-001 / registration surfaces
# ---------------------------------------------------------------------------

def test_claude_code_is_registered_everywhere():
    from agent.backend_provider_contracts import BACKEND_PROVIDER_CONTRACTS
    from agent.cli_backends.routing import (
        CLI_BACKEND_CAPABILITIES,
        CLI_BACKEND_INSTALL_HINTS,
        CLI_BACKEND_VERIFY_COMMANDS,
        SUPPORTED_CLI_BACKENDS,
    )
    from agent.cli_backends.semaphore import _DEFAULT_BACKEND_PARALLEL_LIMITS
    from agent.providers.worker_execution import EXECUTOR_KIND_TO_PROVIDER_ID, normalize_executor_kind

    assert "claude_code" in SUPPORTED_CLI_BACKENDS
    assert "claude_code" in CLI_BACKEND_INSTALL_HINTS
    assert "claude_code" in CLI_BACKEND_VERIFY_COMMANDS
    assert "claude_code" in CLI_BACKEND_CAPABILITIES
    assert _DEFAULT_BACKEND_PARALLEL_LIMITS["claude_code"] == 1
    assert EXECUTOR_KIND_TO_PROVIDER_ID["claude_code"] == "claude_code"
    assert normalize_executor_kind("claude_code") == "claude_code"
    providers = {entry["provider"] for entry in BACKEND_PROVIDER_CONTRACTS}
    assert "claude_code" in providers


def test_claude_code_executor_kind_accepted_in_contract_sets():
    from agent.routes.config.shared import normalize_worker_todo_contract_config
    from agent.services.worker_todo_planner_service import _normalize_executor_kind

    cfg = normalize_worker_todo_contract_config({"default_executor_kind": "claude_code"})
    assert cfg["default_executor_kind"] == "claude_code"
    assert _normalize_executor_kind("claude_code") == "claude_code"


def test_claude_cli_is_assistant_editable_setting():
    from agent.routes.config.read_models import assistant_editable_settings_inventory

    keys = {item["key"] for item in assistant_editable_settings_inventory()}
    assert "claude_cli" in keys


def test_config_defaults_claude_cli_block_is_opt_in():
    from agent.config_defaults import build_default_agent_config

    block = build_default_agent_config()["claude_cli"]
    assert block["enabled"] is False
    assert block["auth_mode"] == "claude_login"
    assert block["permission_mode"] == "plan"
    assert block["write_armed_default"] is False


# ---------------------------------------------------------------------------
# COMMON-003 / diagnose helper + API endpoints
# ---------------------------------------------------------------------------

def test_diagnose_cli_backend_not_installed():
    from agent.cli_backends.routing import diagnose_cli_backend

    settings = _fake_settings()
    with patch("agent.cli_backends.routing.settings", settings), \
         patch("agent.cli_backends.routing.shutil.which", return_value=None):
        result = diagnose_cli_backend("claude_code")
    assert result["status"] == "not_installed"
    assert result["install_hint"] == "npm i -g @anthropic-ai/claude-code"
    assert result["version_probe"] is None


def test_diagnose_cli_backend_ready_with_mock_binary():
    from agent.cli_backends.routing import diagnose_cli_backend

    settings = _fake_settings()
    with patch("agent.cli_backends.routing.settings", settings), \
         patch("agent.cli_backends.routing.shutil.which", return_value="/usr/bin/claude"), \
         patch("subprocess.run", return_value=_completed(0, "1.2.3", "")):
        result = diagnose_cli_backend("claude_code")
    assert result["status"] == "ready"
    assert result["version_probe"]["rc"] == 0
    assert "1.2.3" in result["version_probe"]["stdout"]


def test_diagnose_cli_backend_unsupported():
    from agent.cli_backends.routing import diagnose_cli_backend

    assert diagnose_cli_backend("not-a-backend")["status"] == "unsupported"


def test_backend_health_endpoint_claude(client, admin_auth_header):
    response = client.get("/api/sgpt/backends/claude_code/health", headers=admin_auth_header)
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["backend"] == "claude_code"
    assert data["status"] in {"ready", "not_installed", "disabled"}
    assert "provider" in data
    assert "auth_mode" in (data["provider"] or {})


def test_backend_health_endpoint_unknown_backend_404(client, admin_auth_header):
    response = client.get("/api/sgpt/backends/nonsense/health", headers=admin_auth_header)
    assert response.status_code == 404


def test_backend_diagnose_endpoint(client, admin_auth_header):
    with patch("agent.cli_backends.routing.shutil.which", return_value=None):
        response = client.post("/api/sgpt/backends/claude_code/diagnose", headers=admin_auth_header)
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["status"] == "not_installed"


def test_backend_test_run_endpoint_uses_requested_backend(client, admin_auth_header):
    with patch(
        "agent.routes.sgpt.run_llm_cli_command",
        return_value=(0, "OK", "", "claude_code"),
    ) as mock_run:
        response = client.post(
            "/api/sgpt/backends/claude_code/test-run",
            json={"prompt": "Antworte nur mit OK"},
            headers=admin_auth_header,
        )
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["ok"] is True
    assert data["backend_used"] == "claude_code"
    kwargs = mock_run.call_args.kwargs
    assert kwargs["backend"] == "claude_code"
    assert kwargs["routing_policy"] == {"allowed_backends": ["claude_code"]}


# ---------------------------------------------------------------------------
# COMMON-001 follow-up / write_armed + diff review
# ---------------------------------------------------------------------------

import os
import stat
import subprocess as _subprocess


def _init_git_repo(path) -> None:
    for cmd in (
        ["git", "init", "-q"],
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"],
    ):
        _subprocess.run(cmd, cwd=str(path), check=True, capture_output=True)
    _subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init", "--allow-empty"],
        cwd=str(path), check=True, capture_output=True,
    )


def _write_mock_claude(tmp_path, script_body: str) -> str:
    """Create a real executable mock claude binary (COMMON-005)."""
    binary = tmp_path / "bin" / "claude"
    binary.parent.mkdir(exist_ok=True)
    binary.write_text("#!/bin/sh\n" + script_body + "\n")
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(binary)


def test_run_claude_write_armed_produces_diff_and_leaves_original_untouched(tmp_path):
    from agent.cli_backends.opencode import run_claude_write_armed

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "hello.txt").write_text("original\n")
    _init_git_repo(repo)
    mock_claude = _write_mock_claude(tmp_path, 'echo "modified by claude" > hello.txt\necho OK')

    app = _fake_app({"claude_cli": {"enabled": True, "allowed_paths": [str(tmp_path)]}})
    settings = _fake_settings(claude_path=mock_claude)
    with app.app_context(), patch("agent.cli_backends.opencode.settings", settings):
        result = run_claude_write_armed("aendere hello.txt", timeout=60, workdir=str(repo))

    assert result["status"] == "awaiting_diff_review"
    assert result["rc"] == 0
    assert result["changed_files"] == ["hello.txt"]
    assert "modified by claude" in result["diff"]
    assert result["diff_truncated"] is False
    # Approval-Gate: Original bleibt unveraendert.
    assert (repo / "hello.txt").read_text() == "original\n"


def test_run_claude_write_armed_no_changes(tmp_path):
    from agent.cli_backends.opencode import run_claude_write_armed

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "hello.txt").write_text("original\n")
    _init_git_repo(repo)
    mock_claude = _write_mock_claude(tmp_path, "echo nothing to do")

    app = _fake_app({"claude_cli": {"enabled": True, "allowed_paths": [str(tmp_path)]}})
    settings = _fake_settings(claude_path=mock_claude)
    with app.app_context(), patch("agent.cli_backends.opencode.settings", settings):
        result = run_claude_write_armed("nichts tun", timeout=60, workdir=str(repo))

    assert result["status"] == "no_changes"
    assert result["changed_files"] == []
    assert result["diff"] == ""


def test_run_claude_write_armed_requires_allowed_paths(tmp_path):
    from agent.cli_backends.opencode import run_claude_write_armed

    app = _fake_app({"claude_cli": {"enabled": True}})
    settings = _fake_settings()
    with app.app_context(), patch("agent.cli_backends.opencode.settings", settings):
        result = run_claude_write_armed("x", workdir=str(tmp_path))
    assert result["status"] == "error"
    assert "allowed_paths" in result["stderr"]


def test_run_claude_write_armed_requires_git_repo(tmp_path):
    from agent.cli_backends.opencode import run_claude_write_armed

    plain = tmp_path / "plain"
    plain.mkdir()
    app = _fake_app({"claude_cli": {"enabled": True, "allowed_paths": [str(tmp_path)]}})
    settings = _fake_settings()
    with app.app_context(), patch("agent.cli_backends.opencode.settings", settings):
        result = run_claude_write_armed("x", workdir=str(plain))
    assert result["status"] == "error"
    assert "Git-Repository" in result["stderr"]


def test_run_claude_write_armed_uses_accept_edits_permission_mode(tmp_path):
    """write_armed ist der einzige Pfad mit acceptEdits; der Mock loggt
    seine Argumente zur Verifikation."""
    from agent.cli_backends.opencode import run_claude_write_armed

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "hello.txt").write_text("original\n")
    _init_git_repo(repo)
    args_log = tmp_path / "args.log"
    mock_claude = _write_mock_claude(tmp_path, f'echo "$@" > {args_log}\necho OK')

    app = _fake_app({"claude_cli": {"enabled": True, "allowed_paths": [str(tmp_path)]}})
    settings = _fake_settings(claude_path=mock_claude)
    with app.app_context(), patch("agent.cli_backends.opencode.settings", settings):
        run_claude_write_armed("pruefe args", timeout=60, workdir=str(repo))

    logged = args_log.read_text()
    assert "--permission-mode acceptEdits" in logged


def test_apply_reviewed_diff_e2e_roundtrip(tmp_path):
    """write_armed erzeugt einen Diff, apply_reviewed_diff wendet ihn
    auf das Original an — ohne Commit (Uebernahme bleibt sichtbar)."""
    from agent.cli_backends.opencode import apply_reviewed_diff, run_claude_write_armed

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "hello.txt").write_text("original\n")
    _init_git_repo(repo)
    mock_claude = _write_mock_claude(tmp_path, 'echo "modified by claude" > hello.txt\necho OK')

    app = _fake_app({"claude_cli": {"enabled": True, "allowed_paths": [str(tmp_path)]}})
    settings = _fake_settings(claude_path=mock_claude)
    with app.app_context(), patch("agent.cli_backends.opencode.settings", settings):
        armed = run_claude_write_armed("aendere hello.txt", timeout=60, workdir=str(repo))
        assert armed["status"] == "awaiting_diff_review"
        applied = apply_reviewed_diff(armed["diff"], workdir=str(repo))

    assert applied["status"] == "applied"
    assert applied["applied"] is True
    assert applied["changed_files"] == ["hello.txt"]
    assert (repo / "hello.txt").read_text() == "modified by claude\n"
    # Kein Auto-Commit: die Aenderung steht im git status.
    status = _subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(repo), capture_output=True, text=True, check=True
    ).stdout
    assert "hello.txt" in status


def test_apply_reviewed_diff_conflict_when_local_state_changed(tmp_path):
    from agent.cli_backends.opencode import apply_reviewed_diff, run_claude_write_armed

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "hello.txt").write_text("original\n")
    _init_git_repo(repo)
    mock_claude = _write_mock_claude(tmp_path, 'echo "modified by claude" > hello.txt\necho OK')

    app = _fake_app({"claude_cli": {"enabled": True, "allowed_paths": [str(tmp_path)]}})
    settings = _fake_settings(claude_path=mock_claude)
    with app.app_context(), patch("agent.cli_backends.opencode.settings", settings):
        armed = run_claude_write_armed("aendere hello.txt", timeout=60, workdir=str(repo))
        # Lokaler Stand aendert sich zwischen Review und Apply:
        (repo / "hello.txt").write_text("diverged locally\n")
        applied = apply_reviewed_diff(armed["diff"], workdir=str(repo))

    assert applied["status"] == "conflict"
    assert applied["applied"] is False
    # Original bleibt in seinem lokalen Zustand.
    assert (repo / "hello.txt").read_text() == "diverged locally\n"


def test_apply_reviewed_diff_rejects_empty_and_oversized_diff(tmp_path):
    from agent.cli_backends.opencode import _WRITE_ARMED_MAX_DIFF_CHARS, apply_reviewed_diff

    app = _fake_app({"claude_cli": {"enabled": True, "allowed_paths": [str(tmp_path)]}})
    settings = _fake_settings()
    with app.app_context(), patch("agent.cli_backends.opencode.settings", settings):
        empty = apply_reviewed_diff("", workdir=str(tmp_path))
        oversized = apply_reviewed_diff("x" * (_WRITE_ARMED_MAX_DIFF_CHARS + 1), workdir=str(tmp_path))
    assert empty["status"] == "error"
    assert "Leerer Diff" in empty["stderr"]
    assert oversized["status"] == "error"
    assert "abgeschnitten" in oversized["stderr"]


def test_apply_reviewed_diff_requires_allowed_paths_and_git_repo(tmp_path):
    from agent.cli_backends.opencode import apply_reviewed_diff

    diff = "diff --git a/x b/x\n"
    settings = _fake_settings()
    app = _fake_app({"claude_cli": {"enabled": True}})
    with app.app_context(), patch("agent.cli_backends.opencode.settings", settings):
        no_paths = apply_reviewed_diff(diff, workdir=str(tmp_path))
    assert "allowed_paths" in no_paths["stderr"]

    plain = tmp_path / "plain"
    plain.mkdir()
    app = _fake_app({"claude_cli": {"enabled": True, "allowed_paths": [str(tmp_path)]}})
    with app.app_context(), patch("agent.cli_backends.opencode.settings", settings):
        no_git = apply_reviewed_diff(diff, workdir=str(plain))
    assert "Git-Repository" in no_git["stderr"]


def test_apply_diff_endpoint_validation_and_status_codes(client, admin_auth_header):
    response = client.post("/api/sgpt/backends/claude_code/apply-diff", json={}, headers=admin_auth_header)
    assert response.status_code == 400
    response = client.post(
        "/api/sgpt/backends/claude_code/apply-diff",
        json={"diff": "diff --git a/x b/x"},
        headers=admin_auth_header,
    )
    assert response.status_code == 400

    with patch(
        "agent.cli_backends.opencode.apply_reviewed_diff",
        return_value={"status": "applied", "applied": True, "changed_files": ["x"], "stderr": ""},
    ):
        ok = client.post(
            "/api/sgpt/backends/claude_code/apply-diff",
            json={"diff": "diff --git a/x b/x", "workdir": "/tmp/repo"},
            headers=admin_auth_header,
        )
    assert ok.status_code == 200
    assert ok.get_json()["data"]["applied"] is True

    with patch(
        "agent.cli_backends.opencode.apply_reviewed_diff",
        return_value={"status": "conflict", "applied": False, "changed_files": [], "stderr": "diverged"},
    ):
        conflict = client.post(
            "/api/sgpt/backends/claude_code/apply-diff",
            json={"diff": "diff --git a/x b/x", "workdir": "/tmp/repo"},
            headers=admin_auth_header,
        )
    assert conflict.status_code == 409


def test_write_armed_endpoint_requires_prompt_and_workdir(client, admin_auth_header):
    response = client.post("/api/sgpt/backends/claude_code/write-armed-run", json={}, headers=admin_auth_header)
    assert response.status_code == 400
    response = client.post(
        "/api/sgpt/backends/claude_code/write-armed-run",
        json={"prompt": "x"},
        headers=admin_auth_header,
    )
    assert response.status_code == 400


def test_write_armed_endpoint_returns_diff_artifact(client, admin_auth_header):
    fake_result = {
        "status": "awaiting_diff_review",
        "rc": 0,
        "stdout": "OK",
        "stderr": "",
        "diff": "diff --git a/hello.txt b/hello.txt",
        "diff_truncated": False,
        "changed_files": ["hello.txt"],
        "write_armed": True,
    }
    with patch("agent.cli_backends.opencode.run_claude_write_armed", return_value=dict(fake_result)) as mock_run:
        response = client.post(
            "/api/sgpt/backends/claude_code/write-armed-run",
            json={"prompt": "aendere hello.txt", "workdir": "/tmp/repo"},
            headers=admin_auth_header,
        )
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["status"] == "awaiting_diff_review"
    assert data["changed_files"] == ["hello.txt"]
    assert "duration_ms" in data
    kwargs = mock_run.call_args.kwargs
    assert kwargs["workdir"] == "/tmp/repo"


def test_backends_listing_contains_claude_runtime_target(client, admin_auth_header):
    response = client.get("/api/sgpt/backends", headers=admin_auth_header)
    assert response.status_code == 200
    data = response.get_json()["data"]
    claude_target = data["routing_dimensions"]["claude_runtime_target"]
    assert claude_target["auth_mode"] in {"claude_login", "api_key"}
    codex_target = data["routing_dimensions"]["codex_runtime_target"]
    assert "auth_mode" in codex_target
    assert "api_key_required" in codex_target
