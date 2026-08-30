"""cliproxyapi-005: OpenCode runtime config for CLIProxyAPI.

Acceptance:

* target_provider=cliproxyapi is recognised as a local_openai_backend
* provider_config.provider.cliproxyapi.npm == '@ai-sdk/openai-compatible'
* provider_config.provider.cliproxyapi.options.baseURL points at the
  CLIProxyAPI base_url
* provider_config.model and small_model use 'cliproxyapi/<model>'
* a native OpenCode provider continues to work unchanged
* no test sets real global user config or XDG_CONFIG_HOME persistently

These tests use a minimal Flask app context with AGENT_CONFIG and
PROVIDER_URLS set, which is the documented hook for runtime config
(see ``docs/cli/commands.md`` and ``_get_agent_config`` in
``agent/cli_backends/helpers.py``). No monkeypatching of internal
helpers, no global state mutation, no DB connection.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# shared fixtures (cliproxyapi-004 shared_mock_strategy applied)
# ---------------------------------------------------------------------------

def _cliproxyapi_agent_cfg(*, base_url: str = "http://localhost:8317/v1",
                           default_model: str | None = None) -> dict:
    """Build the agent_cfg that a CLIProxyAPI user would write.

    Models are stored as bare names; the runtime-config resolver
    prefixes them with the active provider id (``cliproxyapi``).
    Storing them as ``codex/gpt-5.5-codex`` would falsely route them
    to the built-in codex provider, not to CLIProxyAPI.
    """
    cfg: dict = {
        "default_provider": "cliproxyapi",
        "local_openai_backends": [
            {
                "id": "cliproxyapi",
                "name": "CLI Proxy API",
                "base_url": base_url,
                "supports_tool_calls": True,
                "models": ["gpt-5.5-codex", "claude-sonnet"],
            }
        ],
    }
    if default_model:
        cfg["default_model"] = default_model
    return cfg


@pytest.fixture
def flask_app_with_agent_config(monkeypatch):
    """Yield a minimal Flask app with AGENT_CONFIG set; tests push
    their own agent_cfg into the config. No DB, no startup, no
    extensions. Provider URLs default to empty to avoid leaking real
    settings.

    We also blank out settings.openai_api_key and
    settings.opencode_default_model. These are sourced from .env in
    the test environment (OPENAI_API_KEY=***, OPENCODE_DEFAULT_MODEL=
    opencode/big-pickle). Without blanking them, the opencode runtime
    config would inject an explicit provider prefix from the env
    value, and the codex config would fall back to the env api key
    before applying the local-dummy fallback.
    """
    from flask import Flask
    app = Flask(__name__)
    app.config["AGENT_CONFIG"] = {}
    app.config["PROVIDER_URLS"] = {}
    app.config["TESTING"] = True
    from agent import config as cfg_mod
    monkeypatch.setattr(cfg_mod.settings, "openai_api_key", None)
    monkeypatch.setattr(cfg_mod.settings, "opencode_default_model", "")
    return app


# ---------------------------------------------------------------------------
# OpenCode runtime-config tests for CLIProxyAPI
# ---------------------------------------------------------------------------

def test_opencode_runtime_config_cliproxyapi_provider_block(flask_app_with_agent_config):
    cfg = _cliproxyapi_agent_cfg()
    flask_app_with_agent_config.config["AGENT_CONFIG"] = cfg
    with flask_app_with_agent_config.app_context():
        from agent.cli_backends.opencode import resolve_opencode_runtime_config
        res = resolve_opencode_runtime_config(model="gpt-5.5-codex")
    assert res["target_provider"] == "cliproxyapi"
    assert res["target_model"] == "gpt-5.5-codex"
    assert res["base_url"] == "http://localhost:8317/v1"
    assert res["base_url_source"] == "local_openai.cliproxyapi"
    pc = res["provider_config"]
    assert pc is not None
    assert "cliproxyapi" in pc["provider"]
    clip_block = pc["provider"]["cliproxyapi"]
    assert clip_block["npm"] == "@ai-sdk/openai-compatible"
    assert clip_block["options"]["baseURL"] == "http://localhost:8317/v1"
    assert "gpt-5.5-codex" in clip_block["models"]


def test_opencode_runtime_config_cliproxyapi_model_names(flask_app_with_agent_config):
    cfg = _cliproxyapi_agent_cfg()
    flask_app_with_agent_config.config["AGENT_CONFIG"] = cfg
    with flask_app_with_agent_config.app_context():
        from agent.cli_backends.opencode import resolve_opencode_runtime_config
        res = resolve_opencode_runtime_config(model="gpt-5.5-codex")
    assert res["provider_config"]["model"] == "cliproxyapi/gpt-5.5-codex"
    assert res["provider_config"]["small_model"] == "cliproxyapi/gpt-5.5-codex"


def test_opencode_runtime_config_cliproxyapi_base_url_without_v1(flask_app_with_agent_config):
    """base_url without /v1 should be normalised to .../v1."""
    cfg = _cliproxyapi_agent_cfg(base_url="http://localhost:8317")
    flask_app_with_agent_config.config["AGENT_CONFIG"] = cfg
    with flask_app_with_agent_config.app_context():
        from agent.cli_backends.opencode import resolve_opencode_runtime_config
        res = resolve_opencode_runtime_config(model="gpt-5.5-codex")
    assert res["base_url"] == "http://localhost:8317/v1"


def test_opencode_runtime_config_cliproxyapi_base_url_with_chat_completions(flask_app_with_agent_config):
    """base_url with /v1/chat/completions suffix is normalised back to /v1."""
    cfg = _cliproxyapi_agent_cfg(
        base_url="http://localhost:8317/v1/chat/completions",
    )
    flask_app_with_agent_config.config["AGENT_CONFIG"] = cfg
    with flask_app_with_agent_config.app_context():
        from agent.cli_backends.opencode import resolve_opencode_runtime_config
        res = resolve_opencode_runtime_config(model="gpt-5.5-codex")
    assert res["base_url"] == "http://localhost:8317/v1"


def test_opencode_runtime_config_cliproxyapi_target_kind_local(flask_app_with_agent_config):
    cfg = _cliproxyapi_agent_cfg()  # localhost -> local
    flask_app_with_agent_config.config["AGENT_CONFIG"] = cfg
    with flask_app_with_agent_config.app_context():
        from agent.cli_backends.opencode import resolve_opencode_runtime_config
        res = resolve_opencode_runtime_config(model="gpt-5.5-codex")
    assert res["target_kind"] == "local_openai"


def test_opencode_runtime_config_cliproxyapi_target_kind_remote(flask_app_with_agent_config):
    cfg = _cliproxyapi_agent_cfg(base_url="https://cliproxyapi.example.com/v1")
    flask_app_with_agent_config.config["AGENT_CONFIG"] = cfg
    with flask_app_with_agent_config.app_context():
        from agent.cli_backends.opencode import resolve_opencode_runtime_config
        res = resolve_opencode_runtime_config(model="gpt-5.5-codex")
    assert res["target_kind"] == "remote_openai_compatible"


def test_opencode_runtime_config_explicit_cliproxyapi_model_in_string(flask_app_with_agent_config):
    """model='cliproxyapi/<model>' is also accepted (explicit provider
    prefix in the model identifier)."""
    cfg = _cliproxyapi_agent_cfg()
    flask_app_with_agent_config.config["AGENT_CONFIG"] = cfg
    with flask_app_with_agent_config.app_context():
        from agent.cli_backends.opencode import resolve_opencode_runtime_config
        res = resolve_opencode_runtime_config(model="cliproxyapi/gpt-5.5-codex")
    assert res["target_provider"] == "cliproxyapi"
    assert res["target_model"] == "gpt-5.5-codex"


def test_opencode_runtime_config_default_model_used_when_no_arg(flask_app_with_agent_config):
    cfg = _cliproxyapi_agent_cfg(default_model="cliproxyapi/claude-sonnet")
    flask_app_with_agent_config.config["AGENT_CONFIG"] = cfg
    with flask_app_with_agent_config.app_context():
        from agent.cli_backends.opencode import resolve_opencode_runtime_config
        res = resolve_opencode_runtime_config()
    assert res["target_provider"] == "cliproxyapi"
    assert res["target_model"] == "claude-sonnet"


# ---------------------------------------------------------------------------
# Native provider regression: existing flows must NOT break
# ---------------------------------------------------------------------------

def test_opencode_runtime_config_native_passthrough_still_works(flask_app_with_agent_config):
    cfg = {
        "default_provider": "opencode",
        "opencode_runtime": {
            "target_provider": "opencode",
        },
    }
    flask_app_with_agent_config.config["AGENT_CONFIG"] = cfg
    with flask_app_with_agent_config.app_context():
        from agent.cli_backends.opencode import resolve_opencode_runtime_config
        # No model argument — this means no explicit_provider from
        # _split_cli_model_identifier. The forced_target_provider is
        # 'opencode', which goes through the native passthrough (Z.
        # 170-172) and produces target_provider=None.
        res = resolve_opencode_runtime_config()
    # Native passthrough (Z. 223): when forced_target_provider is in
    # _native_passthrough, the runtime-config hands off to opencode's
    # own provider config and returns target_provider=None.
    assert res["target_provider"] is None
    assert res["provider_config"] is None


def test_opencode_runtime_config_ollama_unchanged(flask_app_with_agent_config):
    cfg = {
        "default_provider": "ollama",
        "opencode_runtime": {"target_provider": "ollama"},
    }
    flask_app_with_agent_config.config["AGENT_CONFIG"] = cfg
    flask_app_with_agent_config.config["PROVIDER_URLS"] = {
        "ollama": "http://localhost:11434/v1",
    }
    with flask_app_with_agent_config.app_context():
        from agent.cli_backends.opencode import resolve_opencode_runtime_config
        # No provider prefix in the model name — ollama is the
        # target_provider, so the model is bare.
        res = resolve_opencode_runtime_config(model="llama3.1")
    assert res["target_provider"] == "ollama"


def test_opencode_runtime_config_lmstudio_unchanged(flask_app_with_agent_config):
    cfg = {
        "default_provider": "lmstudio",
        "opencode_runtime": {"target_provider": "lmstudio"},
    }
    flask_app_with_agent_config.config["AGENT_CONFIG"] = cfg
    flask_app_with_agent_config.config["PROVIDER_URLS"] = {
        "lmstudio": "http://localhost:1234/v1",
    }
    with flask_app_with_agent_config.app_context():
        from agent.cli_backends.opencode import resolve_opencode_runtime_config
        # The provider prefix in the model name must be "lmstudio",
        # not the publisher name. lmstudio is a built-in provider;
        # it routes via opencode's native lmstudio support.
        res = resolve_opencode_runtime_config(model="lmstudio/Meta-Llama-3")
    assert res["target_provider"] == "lmstudio"


# ---------------------------------------------------------------------------
# Declarative target-provider regression
# ---------------------------------------------------------------------------

def test_opencode_target_provider_cliproxyapi_works_without_default_provider(flask_app_with_agent_config):
    """A configured local provider is a valid explicit OpenCode target."""
    cfg = {
        "opencode_runtime": {"target_provider": "cliproxyapi", "target_model": "gpt-5.5-codex"},
        "local_openai_backends": [
            {
                "id": "cliproxyapi",
                "name": "CLI Proxy API",
                "base_url": "http://localhost:8317/v1",
                "supports_tool_calls": True,
            }
        ],
    }
    flask_app_with_agent_config.config["AGENT_CONFIG"] = cfg
    with flask_app_with_agent_config.app_context():
        from agent.cli_backends.opencode import resolve_opencode_runtime_config
        res = resolve_opencode_runtime_config()
    assert res["target_provider"] == "cliproxyapi"
    assert res["base_url"] == "http://localhost:8317/v1"
    assert res["provider_config"] is not None


def test_opencode_target_provider_cliproxyapi_via_default_provider_works(flask_app_with_agent_config):
    """Workaround: setting default_provider=cliproxyapi keeps the
    target_provider intact and produces a valid provider_config."""
    cfg = _cliproxyapi_agent_cfg()
    flask_app_with_agent_config.config["AGENT_CONFIG"] = cfg
    with flask_app_with_agent_config.app_context():
        from agent.cli_backends.opencode import resolve_opencode_runtime_config
        res = resolve_opencode_runtime_config(model="gpt-5.5-codex")
    assert res["target_provider"] == "cliproxyapi"
    assert res["provider_config"] is not None


# ---------------------------------------------------------------------------
# Codex-runtime-config tests for CLIProxyAPI (cliproxyapi-006 regression)
# ---------------------------------------------------------------------------

def test_codex_runtime_config_cliproxyapi_base_url_resolution(flask_app_with_agent_config):
    cfg = _cliproxyapi_agent_cfg()
    cfg["codex_cli"] = {"target_provider": "cliproxyapi"}
    flask_app_with_agent_config.config["AGENT_CONFIG"] = cfg
    with flask_app_with_agent_config.app_context():
        from agent.cli_backends.opencode import resolve_codex_runtime_config
        res = resolve_codex_runtime_config()
    assert res["target_provider"] == "cliproxyapi"
    assert res["base_url"] == "http://localhost:8317/v1"
    assert res["base_url_source"] == "codex_cli.target_provider:cliproxyapi"
    assert res["target_kind"] == "local_openai"


def test_codex_runtime_config_cliproxyapi_api_key_falls_back_to_local(flask_app_with_agent_config, caplog):
    """If no key is set anywhere, codex falls back to local dummy '***'
    for local base_url (Z. 498–500 of resolve_codex_runtime_config)."""
    cfg = _cliproxyapi_agent_cfg()
    cfg["codex_cli"] = {"target_provider": "cliproxyapi"}
    flask_app_with_agent_config.config["AGENT_CONFIG"] = cfg
    flask_app_with_agent_config.config["PROVIDER_URLS"] = {}
    with flask_app_with_agent_config.app_context():
        from agent.cli_backends.opencode import resolve_codex_runtime_config
        res = resolve_codex_runtime_config()
    # Local-dummy source means the runtime-config decided the key
    # should be the placeholder for local-only routing. The exact
    # placeholder is "***" (or a redacted variant); the
    # *structural* invariant we test is the source.
    assert res["api_key_source"] == "local_dummy"
    assert res["api_key"] is not None


def test_codex_runtime_config_cliproxyapi_remote_no_api_key(flask_app_with_agent_config, monkeypatch):
    """A remote CLIProxyAPI without explicit key gets a clear
    diagnostics message rather than a silent dummy."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "")
    cfg = _cliproxyapi_agent_cfg(
        base_url="https://cliproxyapi.example.com/v1",
    )
    cfg["codex_cli"] = {"target_provider": "cliproxyapi"}
    flask_app_with_agent_config.config["AGENT_CONFIG"] = cfg
    flask_app_with_agent_config.config["PROVIDER_URLS"] = {}
    with flask_app_with_agent_config.app_context():
        from agent import config as cfg_mod
        monkeypatch.setattr(cfg_mod.settings, "openai_api_key", None)
        from agent.cli_backends.opencode import resolve_codex_runtime_config
        res = resolve_codex_runtime_config()
    assert res["target_kind"] == "remote_openai_compatible"
    # Local dummy does NOT apply to remote — but diagnostics should
    # surface the missing key.
    assert res["api_key"] is None
    assert any("api" in d.lower() or "key" in d.lower()
               for d in res["diagnostics"])


# ---------------------------------------------------------------------------
# YAML example e2e: load the official example and feed it through
# ---------------------------------------------------------------------------

def test_yaml_example_produces_valid_opencode_runtime_config(flask_app_with_agent_config):
    example_path = (Path(__file__).resolve().parents[1]
                    / "docs" / "examples" / "cliproxyapi-agent-config.yaml")
    if not example_path.exists():
        pytest.skip("cliproxyapi-agent-config.yaml not present yet")
    import yaml
    cfg = yaml.safe_load(example_path.read_text(encoding="utf-8"))
    flask_app_with_agent_config.config["AGENT_CONFIG"] = cfg
    with flask_app_with_agent_config.app_context():
        from agent.cli_backends.opencode import resolve_opencode_runtime_config
        res = resolve_opencode_runtime_config()
    assert res["target_provider"] == "cliproxyapi"
    assert res["target_model"] == "gpt-5.5-codex"
    pc = res["provider_config"]
    assert pc is not None
    assert pc["provider"]["cliproxyapi"]["options"]["baseURL"] == "http://localhost:8317/v1"


def test_yaml_example_produces_valid_codex_runtime_config(flask_app_with_agent_config):
    example_path = (Path(__file__).resolve().parents[1]
                    / "docs" / "examples" / "cliproxyapi-agent-config.yaml")
    if not example_path.exists():
        pytest.skip("cliproxyapi-agent-config.yaml not present yet")
    import yaml
    cfg = yaml.safe_load(example_path.read_text(encoding="utf-8"))
    cfg["codex_cli"] = {"target_provider": "cliproxyapi"}
    flask_app_with_agent_config.config["AGENT_CONFIG"] = cfg
    with flask_app_with_agent_config.app_context():
        from agent.cli_backends.opencode import resolve_codex_runtime_config
        res = resolve_codex_runtime_config()
    assert res["base_url"] == "http://localhost:8317/v1"
