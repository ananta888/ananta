"""CCA-002: codex account-login mode without API-key requirement.

Acceptance criteria from the todo:

1. Existing API-key mode remains unchanged usable.
2. New auth_mode=chatgpt_login/account_login is configurable
   (agent_cfg.codex_cli.auth_mode).
3. When auth_mode=chatgpt_login, OPENAI_API_KEY is not validated or
   required.
4. Health/auth check verifies only CLI availability and login status
   via the official CLI usage, without reading token files.
5. Failure cases not_installed, not_logged_in, rate_limited,
   provider_error and timeout are clearly distinguished.
6. Tests cover API-key mode and account-login mode with mock codex.

This file is split from tests/test_codex_cli_backend.py and
test_codex_cli_backend_preflight.py to keep source files below 1000
lines (see the existing comment in
tests/test_codex_cli_backend_preflight.py:6).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# shared mock helpers
# ---------------------------------------------------------------------------

from types import SimpleNamespace

def _fake_settings(*, codex_path: str = "codex",
                  codex_auth_mode: str = "api_key",
                  codex_require_api_key: bool = True) -> SimpleNamespace:
    """Return a simple object that looks like settings for the
    codex auth_mode/require_api_key fields. The real settings has
    many other fields; we mock the ones the preflight/runtime-config
    paths read at the relevant points. Routing.py reads several
    *_path fields (aider_path, mistral_code_path, etc.) so we
    populate them with sensible defaults."""
    return SimpleNamespace(
        codex_path=codex_path,
        codex_default_model="gpt-5-codex",
        codex_auth_mode=codex_auth_mode,
        codex_require_api_key=codex_require_api_key,
        openai_api_key=None,
        openai_url=None,
        ollama_url=None,
        lmstudio_url=None,
        anthropic_url=None,
        mock_url=None,
        default_provider="lmstudio",
        max_prompt_tokens=128000,
        # routing.py reads these *_path fields via _resolve_backend_binary
        aider_path="aider",
        mistral_code_path="mistral-code",
        opencode_path="opencode",
        sgpt_path="sgpt",
        ananta_worker_path="ananta-worker",
        opencode_default_model="opencode/big-pickle",
        aider_default_model="aider-default",
        mistral_code_default_model="mistral-default",
    )


def _fake_app(agent_cfg: dict, *, provider_urls: dict | None = None) -> Any:
    """Return a minimal Flask app with AGENT_CONFIG/PROVIDER_URLS set."""
    from flask import Flask
    app = Flask(__name__)
    app.config["AGENT_CONFIG"] = agent_cfg
    app.config["PROVIDER_URLS"] = provider_urls or {}
    app.config["TESTING"] = True
    return app


# ---------------------------------------------------------------------------
# CCA-002 / auth_mode resolution tests
# ---------------------------------------------------------------------------

def test_resolve_codex_runtime_config_default_auth_mode_is_api_key():
    """Backward-compat: a config without explicit auth_mode keeps the
    legacy 'api_key' default."""
    from agent.cli_backends.opencode import resolve_codex_runtime_config
    app = _fake_app({"codex_cli": {"base_url": "http://localhost:8317/v1"}})
    settings = _fake_settings()
    with app.app_context(), patch("agent.cli_backends.opencode.settings", settings):
        res = resolve_codex_runtime_config()
    assert res["auth_mode"] == "api_key"
    assert res["api_key_required"] is True


def test_resolve_codex_runtime_config_chatgpt_login_disables_api_key_requirement():
    """auth_mode=chatgpt_login implies api_key_required=False, even if
    api_key_required is explicitly set to True."""
    from agent.cli_backends.opencode import resolve_codex_runtime_config
    app = _fake_app({
        "codex_cli": {
            "base_url": "https://api.example.com/v1",
            "auth_mode": "chatgpt_login",
            "api_key_required": True,
        }
    })
    settings = _fake_settings()
    with app.app_context(), patch("agent.cli_backends.opencode.settings", settings):
        res = resolve_codex_runtime_config()
    assert res["auth_mode"] == "chatgpt_login"
    assert res["api_key_required"] is False


def test_resolve_codex_runtime_config_chatgpt_login_keeps_api_key_optional():
    """A configured api_key_profile is honoured but the auth_mode still
    surfaces as chatgpt_login. The api_key_required flag stays
    False so the runtime-config skip the api_key validation."""
    from agent.cli_backends.opencode import resolve_codex_runtime_config
    app = _fake_app({
        "codex_cli": {
            "base_url": "https://api.example.com/v1",
            "auth_mode": "chatgpt_login",
            "api_key_profile": "codex-account",
        },
        "llm_api_key_profiles": {"codex-account": {"api_key": "***"}},
    })
    settings = _fake_settings()
    with app.app_context(), patch("agent.cli_backends.opencode.settings", settings):
        res = resolve_codex_runtime_config()
    assert res["auth_mode"] == "chatgpt_login"
    assert res["api_key_required"] is False
    # The api_key is still resolved if a profile is configured —
    # but the runtime-config run_codex_command will not pass it as
    # OPENAI_API_KEY in chatgpt_login mode.
    assert res["api_key"] == "***"


def test_resolve_codex_runtime_config_unknown_auth_mode_falls_back():
    """Unknown values are treated as 'api_key' (fail-safe default)."""
    from agent.cli_backends.opencode import resolve_codex_runtime_config
    app = _fake_app({
        "codex_cli": {
            "base_url": "http://localhost:8317/v1",
            "auth_mode": "magic-mode",
        }
    })
    settings = _fake_settings()
    with app.app_context(), patch("agent.cli_backends.opencode.settings", settings):
        res = resolve_codex_runtime_config()
    assert res["auth_mode"] == "api_key"


def test_resolve_codex_runtime_config_chatgpt_login_via_settings_only():
    """When agent_cfg.codex_cli.auth_mode is not set, settings.codex_auth_mode
    is honoured."""
    from agent.cli_backends.opencode import resolve_codex_runtime_config
    app = _fake_app({"codex_cli": {"base_url": "http://localhost:8317/v1"}})
    settings = _fake_settings(codex_auth_mode="chatgpt_login")
    with app.app_context(), patch("agent.cli_backends.opencode.settings", settings):
        res = resolve_codex_runtime_config()
    assert res["auth_mode"] == "chatgpt_login"
    assert res["api_key_required"] is False


# ---------------------------------------------------------------------------
# CCA-002 / run_codex_command auth_mode branching
# ---------------------------------------------------------------------------

def test_run_codex_command_api_key_remote_requires_key():
    """Legacy api_key mode: a remote endpoint without api_key fails
    with a clear message. This is the pre-existing behaviour; the
    test pins it to catch regressions."""
    from agent.cli_backends import opencode as opencode_mod
    from agent.cli_backends import sgpt as sgpt_mod

    agent_cfg = {
        "codex_cli": {
            "base_url": "https://api.openai.com/v1",
            "auth_mode": "api_key",
            "api_key_required": True,
        }
    }
    app = _fake_app(agent_cfg, provider_urls={})
    settings = _fake_settings()

    with app.app_context(), patch("agent.cli_backends.opencode.settings", settings), \
         patch("agent.cli_backends.sgpt.settings", settings), \
         patch("agent.cli_backends.opencode.shutil.which", return_value="/usr/bin/codex"), \
         patch("agent.cli_backends.sgpt.shutil.which", return_value="/usr/bin/codex"):
        rc, out, err = sgpt_mod.run_codex_command(prompt="hi", timeout=5)
    assert rc == -1
    assert "api key" in err.lower()


def test_run_codex_command_chatgpt_login_skips_api_key_for_remote():
    """CCA-002 acceptance: chatgpt_login mode runs codex without
    OPENAI_API_KEY being set, even for a remote base_url."""
    from agent.cli_backends import opencode as opencode_mod
    from agent.cli_backends import sgpt as sgpt_mod

    agent_cfg = {
        "codex_cli": {
            "base_url": "https://api.openai.com/v1",
            "auth_mode": "chatgpt_login",
        }
    }
    app = _fake_app(agent_cfg, provider_urls={})
    settings = _fake_settings()

    fake_result = type("R", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    with app.app_context(), patch("agent.cli_backends.opencode.settings", settings), \
         patch("agent.cli_backends.sgpt.settings", settings), \
         patch("agent.cli_backends.opencode.shutil.which", return_value="/usr/bin/codex"), \
         patch("agent.cli_backends.sgpt.shutil.which", return_value="/usr/bin/codex"), \
         patch("subprocess.run", return_value=fake_result) as mock_run:
        rc, out, err = sgpt_mod.run_codex_command(prompt="hi", timeout=5)

    assert rc == 0
    assert out == "ok"
    # The crucial assertion: OPENAI_API_KEY is *not* in the env
    # passed to subprocess.run.
    call_kwargs = mock_run.call_args.kwargs
    env_passed = call_kwargs.get("env") or {}
    assert "OPENAI_API_KEY" not in env_passed
    # OPENAI_BASE_URL is still set so codex can talk to the
    # configured remote.
    assert env_passed.get("OPENAI_BASE_URL") == "https://api.openai.com/v1"
    assert env_passed.get("OPENAI_API_BASE") == "https://api.openai.com/v1"
    assert "stdin" not in call_kwargs
    assert call_kwargs.get("input") == "hi"
    command = mock_run.call_args.args[0]
    assert 'openai_base_url="https://api.openai.com/v1"' in command
    assert "--model" not in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert command[-1] == "-"


def test_run_codex_command_chatgpt_login_local_works_without_any_key():
    """The most natural setup: chatgpt_login + local base_url. Codex
    CLI uses ~/.codex/auth.json, ananta injects nothing."""
    from agent.cli_backends import sgpt as sgpt_mod

    agent_cfg = {
        "codex_cli": {
            "base_url": "http://localhost:8317/v1",
            "auth_mode": "chatgpt_login",
        }
    }
    app = _fake_app(agent_cfg, provider_urls={})
    settings = _fake_settings()

    fake_result = type("R", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    with app.app_context(), patch("agent.cli_backends.opencode.settings", settings), \
         patch("agent.cli_backends.sgpt.settings", settings), \
         patch("agent.cli_backends.opencode.shutil.which", return_value="/usr/bin/codex"), \
         patch("agent.cli_backends.sgpt.shutil.which", return_value="/usr/bin/codex"), \
         patch("subprocess.run", return_value=fake_result) as mock_run:
        rc, out, err = sgpt_mod.run_codex_command(prompt="hi", timeout=5)
    assert rc == 0
    env_passed = mock_run.call_args.kwargs.get("env") or {}
    assert "OPENAI_API_KEY" not in env_passed
    assert env_passed.get("OPENAI_BASE_URL") == "http://localhost:8317/v1"
    assert mock_run.call_args.kwargs.get("input") == "hi"


# ---------------------------------------------------------------------------
# CCA-002 / preflight auth_mode + login_command
# ---------------------------------------------------------------------------

def test_preflight_codex_exposes_auth_mode_for_api_key():
    from agent.cli_backends.routing import get_cli_backend_preflight
    app = _fake_app({
        "codex_cli": {"base_url": "http://localhost:8317/v1"},
    })
    settings = _fake_settings()
    with app.app_context(), patch("agent.cli_backends.opencode.settings", settings), \
         patch("agent.cli_backends.routing.settings", settings):
        result = get_cli_backend_preflight()
    codex = result["providers"]["codex"]
    assert codex["auth_mode"] == "api_key"
    assert codex["api_key_required"] is True
    assert codex["login_command"] is None  # api_key mode has no manual login


def test_preflight_codex_exposes_login_command_for_chatgpt_login():
    from agent.cli_backends.routing import get_cli_backend_preflight
    app = _fake_app({
        "codex_cli": {
            "base_url": "http://localhost:8317/v1",
            "auth_mode": "chatgpt_login",
        },
    })
    settings = _fake_settings()
    with app.app_context(), patch("agent.cli_backends.opencode.settings", settings), \
         patch("agent.cli_backends.routing.settings", settings):
        result = get_cli_backend_preflight()
    codex = result["providers"]["codex"]
    assert codex["auth_mode"] == "chatgpt_login"
    assert codex["api_key_required"] is False
    assert codex["login_command"] == "codex login"


def test_preflight_codex_login_command_respects_codex_path():
    """The login_command hint must use the configured codex_path, not
    a hardcoded 'codex' — users with CODEX_PATH=/opt/bin/codex need
    the hint to match."""
    from agent.cli_backends.routing import get_cli_backend_preflight
    app = _fake_app({
        "codex_cli": {
            "base_url": "http://localhost:8317/v1",
            "auth_mode": "chatgpt_login",
        },
    })
    settings = _fake_settings(codex_path="/opt/bin/codex")
    with app.app_context(), patch("agent.cli_backends.opencode.settings", settings), \
         patch("agent.cli_backends.routing.settings", settings):
        result = get_cli_backend_preflight()
    codex = result["providers"]["codex"]
    assert codex["login_command"] == "/opt/bin/codex login"


# ---------------------------------------------------------------------------
# CCA-002 / failure-mode distinction
# ---------------------------------------------------------------------------

def test_run_codex_command_not_installed_returns_clear_error():
    """The 'binary not found' error path is already in place (CCA-001);
    this test pins the error string so it is clearly distinguishable
    from auth errors."""
    from agent.cli_backends import sgpt as sgpt_mod

    app = _fake_app({})
    settings = _fake_settings()
    with app.app_context(), patch("agent.cli_backends.sgpt.settings", settings), \
         patch("agent.cli_backends.sgpt.shutil.which", return_value=None):
        rc, out, err = sgpt_mod.run_codex_command(prompt="hi", timeout=5)
    assert rc == -1
    assert "not found" in err.lower() or "not_found" in err.lower()


def test_run_codex_command_timeout_returns_clear_error():
    """A timeout is reported with rc=-1 and a 'Timeout' message;
    this is distinguishable from auth errors via stderr content."""
    from agent.cli_backends import sgpt as sgpt_mod
    import subprocess

    app = _fake_app({
        "codex_cli": {"base_url": "http://localhost:8317/v1"},
    })
    settings = _fake_settings()
    with app.app_context(), patch("agent.cli_backends.opencode.settings", settings), \
         patch("agent.cli_backends.sgpt.settings", settings), \
         patch("agent.cli_backends.opencode.shutil.which", return_value="/usr/bin/codex"), \
         patch("agent.cli_backends.sgpt.shutil.which", return_value="/usr/bin/codex"), \
         patch("subprocess.run", side_effect=subprocess.TimeoutExpired("codex", 5)):
        rc, out, err = sgpt_mod.run_codex_command(prompt="hi", timeout=5)
    assert rc == -1
    assert "timeout" in err.lower()


# ---------------------------------------------------------------------------
# CCA-002 / no shell=True invariant (regression guard)
# ---------------------------------------------------------------------------

def test_run_codex_command_never_uses_shell_true():
    """Static guard: subprocess.run inside run_codex_command must not
    use shell=True. This is a regression guard for COMMON-001
    discipline."""
    from agent.cli_backends import opencode as opencode_mod
    import inspect
    src = inspect.getsource(opencode_mod.run_codex_command)
    assert "shell=True" not in src
