"""cliproxyapi-007: preflight label for CLIProxyAPI.

Acceptance:

* preflight shows cliproxyapi with provider, name, base_url,
  supports_tool_calls and provider_type
* unconfigured CLIProxyAPI produces no error and no false warning
* existing LM Studio / Ollama preflight output stays compatible

Decision rationale (from the todo's decision_trigger):

  The "Kleines Label" option was chosen because:

  1. It is purely additive — adding a ``display_name`` key to a
     preflight entry is a new field; existing preflight consumers
     ignore it.
  2. It does not change the format of LM Studio / Ollama entries.
  3. It costs no extra HTTP call (the "Optionaler Health-Hinweis"
     option would have required deciding which model_url to ping).
  4. It matches the documented CLIProxyAPI profile name in
     docs/integrations/cliproxyapi.md.
"""
from __future__ import annotations

from pathlib import Path

import pytest


def _make_preflight_callable(agent_cfg: dict, provider_urls: dict | None = None):
    """Import the preflight function and bind AGENT_CONFIG / PROVIDER_URLS
    via a minimal Flask app context (no DB, no startup)."""
    from flask import Flask
    app = Flask(__name__)
    app.config["AGENT_CONFIG"] = agent_cfg
    app.config["PROVIDER_URLS"] = provider_urls or {}
    app.config["TESTING"] = True
    return app, lambda: app.app_context()


def test_preflight_cliproxyapi_appears_with_display_name():
    from agent.cli_backends.routing import get_cli_backend_preflight
    agent_cfg = {
        "default_provider": "cliproxyapi",
        "local_openai_backends": [
            {
                "id": "cliproxyapi",
                "name": "CLI Proxy API",
                "base_url": "http://localhost:8317/v1",
                "supports_tool_calls": True,
            }
        ],
    }
    app, ctx_fn = _make_preflight_callable(agent_cfg)
    with ctx_fn():
        result = get_cli_backend_preflight()
    local = result["providers"]["local_openai"]
    matches = [e for e in local if e["provider"] == "cliproxyapi"]
    assert len(matches) == 1
    entry = matches[0]
    assert entry["name"] == "CLI Proxy API"
    assert entry["base_url"] == "http://localhost:8317/v1"
    assert entry["supports_tool_calls"] is True
    assert entry["provider_type"] == "local_openai_compatible"
    # The display_name is the *additional* label (cliproxyapi-007).
    assert entry["display_name"] == "CLI Proxy API"


def test_preflight_unconfigured_cliproxyapi_is_silent():
    """A user who has not configured cliproxyapi must not see a
    warning or error in the preflight."""
    from agent.cli_backends.routing import get_cli_backend_preflight
    app, ctx_fn = _make_preflight_callable({})
    with ctx_fn():
        result = get_cli_backend_preflight()
    local = result["providers"]["local_openai"]
    # lmstudio is always there; cliproxyapi is not.
    assert all(e["provider"] != "cliproxyapi" for e in local)


def test_preflight_lmstudio_entry_unchanged():
    """Adding cliproxyapi's display_name must not affect LM Studio
    preflight entries. Their display_name is None (or absent)."""
    from agent.cli_backends.routing import get_cli_backend_preflight
    agent_cfg = {
        "default_provider": "cliproxyapi",
        "local_openai_backends": [
            {
                "id": "cliproxyapi",
                "name": "CLI Proxy API",
                "base_url": "http://localhost:8317/v1",
                "supports_tool_calls": True,
            }
        ],
    }
    app, ctx_fn = _make_preflight_callable(agent_cfg)
    with ctx_fn():
        result = get_cli_backend_preflight()
    local = result["providers"]["local_openai"]
    lmstudio = [e for e in local if e["provider"] == "lmstudio"]
    assert len(lmstudio) == 1
    # LM Studio has no display_name; the field is None for
    # backwards-compat.
    assert lmstudio[0].get("display_name") is None


def test_preflight_ollama_entry_unchanged():
    """Adding cliproxyapi's display_name must not affect Ollama entries.

    Ollama only appears in the preflight when its base_url is
    configured. The test asserts that *if* an Ollama entry exists,
    its display_name field is None (not 'CLI Proxy API').
    """
    from agent.cli_backends.routing import get_cli_backend_preflight
    agent_cfg = {
        "local_openai_backends": [
            {
                "id": "cliproxyapi",
                "name": "CLI Proxy API",
                "base_url": "http://localhost:8317/v1",
                "supports_tool_calls": True,
            }
        ],
    }
    app, ctx_fn = _make_preflight_callable(
        agent_cfg, provider_urls={"ollama": "http://localhost:11434/v1"},
    )
    with ctx_fn():
        result = get_cli_backend_preflight()
    local = result["providers"]["local_openai"]
    ollama = [e for e in local if e["provider"] == "ollama"]
    # If ollama is in the preflight, its display_name must be None.
    for entry in ollama:
        assert entry.get("display_name") is None


def test_preflight_cliproxyapi_case_insensitive_id():
    """'CLIPROXYAPI' (uppercase) and 'cliproxyapi' both get the
    display_name — matching the local_openai_backends dedup behaviour."""
    from agent.cli_backends.routing import get_cli_backend_preflight
    agent_cfg = {
        "default_provider": "cliproxyapi",
        "local_openai_backends": [
            {
                "id": "CLIPROXYAPI",
                "name": "CLI Proxy API",
                "base_url": "http://localhost:8317/v1",
                "supports_tool_calls": True,
            }
        ],
    }
    app, ctx_fn = _make_preflight_callable(agent_cfg)
    with ctx_fn():
        result = get_cli_backend_preflight()
    local = result["providers"]["local_openai"]
    matches = [e for e in local if e["provider"].lower() == "cliproxyapi"]
    assert len(matches) == 1
    assert matches[0]["display_name"] == "CLI Proxy API"


def test_preflight_cliproxyapi_required_fields_present():
    """All required preflight fields are exposed for cliproxyapi."""
    from agent.cli_backends.routing import get_cli_backend_preflight
    agent_cfg = {
        "local_openai_backends": [
            {
                "id": "cliproxyapi",
                "name": "CLI Proxy API",
                "base_url": "http://localhost:8317/v1",
                "supports_tool_calls": True,
            }
        ],
    }
    app, ctx_fn = _make_preflight_callable(agent_cfg)
    with ctx_fn():
        result = get_cli_backend_preflight()
    local = result["providers"]["local_openai"]
    entry = next(e for e in local if e["provider"] == "cliproxyapi")
    required = {"provider", "name", "base_url", "supports_tool_calls",
                "provider_type", "display_name"}
    assert required.issubset(set(entry.keys()))


def test_preflight_no_false_warning_for_cliproxyapi():
    """A user who has not configured cliproxyapi must not see a
    warning, error, or 'cliproxyapi' string anywhere in the
    preflight output (other than as part of a generic CLI-backend
    capability)."""
    from agent.cli_backends.routing import get_cli_backend_preflight
    app, ctx_fn = _make_preflight_callable({})
    with ctx_fn():
        result = get_cli_backend_preflight()
    # No warnings in the local_openai section for missing cliproxyapi.
    local = result["providers"]["local_openai"]
    assert local == [] or all(
        "cliproxyapi" not in str(e.get("warnings") or [])
        for e in local
    )
    # And nothing in the top-level warnings references cliproxyapi.
    flat = json.dumps(result)
    assert "cliproxyapi" not in flat or "cliproxyapi" in {
        e.get("provider") for e in local
    }


import json
