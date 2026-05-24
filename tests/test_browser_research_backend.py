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
        "actions": [{"type": "extract", "target": "body"}],
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
