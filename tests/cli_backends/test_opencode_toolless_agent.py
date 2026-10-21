"""The toolless agent switch must also cover local backends (llamacpp).

`resolve_opencode_runtime_config` builds the toolless `ananta-worker` agent only
for ollama/lmstudio, so `toolless` was silently inert for a local
`local_openai_backends` target (llamacpp): the loop brain kept the full-tool
default agent and timed out exactly as before the toolless fix.
"""

from __future__ import annotations

import pytest


def _llamacpp_agent_cfg() -> dict:
    return {
        "default_provider": "llamacpp",
        "default_model": "ananta-code-27b",
        "local_openai_backends": [
            {
                "id": "llamacpp",
                "name": "llama.cpp server",
                "base_url": "http://llamacpp:18150/v1",
                "supports_tool_calls": True,
                "models": ["ananta-code-27b"],
            }
        ],
        "opencode_runtime": {
            "target_provider": "llamacpp",
            "target_model": "ananta-code-27b",
            "execution_mode": "backend",
        },
    }


@pytest.fixture
def flask_app_with_agent_config(monkeypatch):
    from flask import Flask

    app = Flask(__name__)
    app.config["AGENT_CONFIG"] = {}
    app.config["PROVIDER_URLS"] = {}
    app.config["TESTING"] = True
    from agent import config as cfg_mod

    monkeypatch.setattr(cfg_mod.settings, "openai_api_key", None)
    monkeypatch.setattr(cfg_mod.settings, "opencode_default_model", "")
    return app


def test_toolless_builds_the_toolless_agent_for_a_local_backend(flask_app_with_agent_config):
    flask_app_with_agent_config.config["AGENT_CONFIG"] = _llamacpp_agent_cfg()
    with flask_app_with_agent_config.app_context():
        from agent.cli_backends.opencode import resolve_opencode_runtime_config

        res = resolve_opencode_runtime_config(model="ananta-code-27b", tool_mode="toolless")
    assert res["target_provider"] == "llamacpp"
    assert res["tool_mode"] == "toolless"
    pc = res["provider_config"]
    assert pc is not None
    assert pc["default_agent"] == "ananta-worker"
    assert "ananta-worker" in pc["agent"]
    # the plain toolless agent keeps its CodeCompass prompt (native MCP users)
    assert "Use CodeCompass tools" in pc["agent"]["ananta-worker"]["prompt"]


def test_protocol_builds_the_loop_brain_agent(flask_app_with_agent_config):
    flask_app_with_agent_config.config["AGENT_CONFIG"] = _llamacpp_agent_cfg()
    with flask_app_with_agent_config.app_context():
        from agent.cli_backends.opencode import resolve_opencode_runtime_config

        res = resolve_opencode_runtime_config(model="ananta-code-27b", tool_mode="protocol")
    assert res["tool_mode"] == "protocol"
    pc = res["provider_config"]
    assert pc is not None
    assert pc["default_agent"] == "ananta-worker"
    brain = pc["agent"]["ananta-worker"]
    assert "JSON protocol object" in brain["prompt"]
    # the brain must forward the loop's own mandates (codecompass steps) instead of
    # promising them away — that promise made the model skip the analysis calls
    assert "codecompass analysis calls" in brain["prompt"]
    assert "never use your own tools" in brain["prompt"]
    assert "Use CodeCompass tools" not in brain["prompt"]
    assert all(value is False for value in brain["tools"].values())


def test_full_mode_keeps_the_default_agent(flask_app_with_agent_config):
    flask_app_with_agent_config.config["AGENT_CONFIG"] = _llamacpp_agent_cfg()
    with flask_app_with_agent_config.app_context():
        from agent.cli_backends.opencode import resolve_opencode_runtime_config

        res = resolve_opencode_runtime_config(model="ananta-code-27b", tool_mode="full")
    assert res["tool_mode"] == "full"
    pc = res["provider_config"]
    assert pc is not None
    assert "default_agent" not in pc
