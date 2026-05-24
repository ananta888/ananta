from __future__ import annotations

import json

from agent import research_backend as rb


def test_e2e_browser_use_allowed_domain_mock():
    ctx = {
        "start_url": "https://example.com",
        "actions": [{"type": "extract", "target": "#content"}],
        "browser_config": {
            "enabled": True,
            "command": "bash -lc true",
            "allowed_domains": ["example.com"],
            "max_actions": 2,
            "timeout_seconds": 10,
            "download_policy": "deny",
            "auth_policy": "none",
            "screenshot_policy": "none",
        },
    }
    rc, out, err = rb._execute_research_backend_cli(prompt="collect", provider="browser_use", timeout=5, research_context=ctx)
    assert rc == 0
    payload = json.loads(out)
    assert payload["sources"][0]["url"].startswith("https://example.com")


def test_e2e_browser_use_blocked_domain_mock():
    ctx = {
        "start_url": "https://blocked.example.net",
        "actions": [{"type": "extract", "target": "#content"}],
        "browser_config": {
            "enabled": True,
            "command": "bash -lc true",
            "allowed_domains": ["example.com"],
            "max_actions": 2,
            "timeout_seconds": 10,
            "download_policy": "deny",
            "auth_policy": "none",
            "screenshot_policy": "none",
        },
    }
    rc, out, err = rb._execute_research_backend_cli(prompt="collect", provider="browser_use", timeout=5, research_context=ctx)
    assert rc != 0
    assert "security_denied" in err or "domain_not_allowed" in err
