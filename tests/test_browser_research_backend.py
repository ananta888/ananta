from __future__ import annotations

import json

from agent import research_backend as rb


def test_browser_use_disabled_without_context_enable():
    rc, out, err = rb._execute_research_backend_cli(prompt="x", provider="browser_use", timeout=5, research_context={})
    assert rc != 0
    assert "disabled" in err


def test_browser_use_exec_success_with_mock_actions():
    ctx = {
        "start_url": "https://example.com",
        "actions": [{"type": "click", "target": "#a"}, {"type": "click", "target": "#b"}],
        "browser_config": {
            "enabled": True,
            "command": "bash -lc true",
            "allowed_domains": ["example.com"],
            "max_actions": 3,
            "timeout_seconds": 10,
            "download_policy": "deny",
            "auth_policy": "none",
            "screenshot_policy": "none",
        },
    }
    rc, out, err = rb._execute_research_backend_cli(prompt="x", provider="browser_use", timeout=5, research_context=ctx)
    assert rc == 0
    payload = json.loads(out)
    assert "extracted_data" in payload


def test_browser_diagnostics_shape():
    d = rb.get_browser_backend_diagnostics()
    assert d["provider"] == "browser_use"
    assert "calls" in d


def test_browser_health_payload_contains_readiness():
    health = rb.get_browser_backend_health(
        {
            "enabled": True,
            "configured": True,
            "mode": "native",
            "command": "bash -lc true",
        }
    )
    assert health["provider"] == "browser_use"
    assert "ready" in health


def test_browser_health_invalid_provider_mode():
    health = rb.get_browser_backend_health(
        {
            "enabled": True,
            "configured": True,
            "mode": "native",
            "provider_mode": "invalid-mode",
            "command": "bash -lc true",
        }
    )
    assert health["ready"] is False
    assert health["reason"] == "browser_backend_invalid_provider_mode"


def test_browser_auth_requires_explicit_opt_in():
    ctx = {
        "start_url": "https://example.com",
        "actions": [{"type": "extract", "target": "body"}],
        "browser_config": {
            "enabled": True,
            "command": "bash -lc true",
            "allowed_domains": ["example.com"],
            "max_actions": 3,
            "timeout_seconds": 10,
            "download_policy": "deny",
            "auth_policy": "none",
            "auth_requested": True,
            "screenshot_policy": "none",
        },
    }
    rc, out, err = rb._execute_research_backend_cli(prompt="x", provider="browser_use", timeout=5, research_context=ctx)
    assert rc != 0
    assert "auth_not_allowed" in err


def test_browser_needs_review_escalation_payload():
    ctx = {
        "start_url": "https://example.com",
        "actions": [{"type": "extract", "target": "body"}],
        "browser_config": {
            "enabled": True,
            "command": "bash -lc true",
            "allowed_domains": ["example.com"],
            "max_actions": 1,
            "timeout_seconds": 10,
            "download_policy": "deny",
            "auth_policy": "none",
            "screenshot_policy": "none",
            "max_repair_attempts": 0,
            "fallback_allowed": True,
        },
    }
    rc, out, err = rb._execute_research_backend_cli(prompt="x", provider="browser_use", timeout=5, research_context=ctx)
    assert rc != 0
    assert "browser_needs_review:" in err


def test_browser_audit_event_family_and_download_policy_trace(monkeypatch):
    events = []

    def _capture(event_type, payload):
        events.append((event_type, dict(payload or {})))

    monkeypatch.setattr(rb, "log_audit", _capture)
    ctx = {
        "start_url": "https://example.com",
        "actions": [
            {"type": "download", "url": "https://example.com/file.txt", "output_path": "/tmp/out/file.txt"},
            {"type": "extract", "target": "body"},
        ],
        "browser_config": {
            "enabled": True,
            "command": "bash -lc true",
            "allowed_domains": ["example.com"],
            "max_actions": 5,
            "timeout_seconds": 10,
            "download_policy": "bounded_output_dir",
            "output_dir": "/tmp/out",
            "auth_policy": "none",
            "screenshot_policy": "none",
        },
    }
    rc, out, err = rb._execute_research_backend_cli(prompt="x", provider="browser_use", timeout=5, research_context=ctx)
    assert rc == 0
    assert err == ""
    names = [item[0] for item in events]
    assert "browser_route_selected" in names
    assert "browser_policy_checked" in names
    assert "browser_action_executed" in names
    assert "browser_artifact_verified" in names
