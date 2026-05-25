from __future__ import annotations

from unittest.mock import patch

from agent import research_backend as rb


def test_browser_use_adapter_selected_for_research_backend_command():
    with patch("agent.research_backend._execute_research_backend_cli", return_value=(0, '{"ok":true}', "")):
        rc, out, err = rb.run_research_backend_command(
            prompt="collect",
            provider="browser_use",
            research_context={
                "start_url": "https://example.com",
                "actions": [{"type": "extract", "target": "body"}],
                "browser_config": {
                    "enabled": True,
                    "command": "bash -lc true",
                    "allowed_domains": ["example.com"],
                },
            },
        )
    assert rc == 0
    assert out
    assert err == ""


def test_non_browser_provider_mapping_unchanged():
    assert rb.get_research_backend_adapter("deerflow").provider == "deerflow"
    assert rb.get_research_backend_adapter("ananta_research").provider == "ananta_research"
    assert rb.get_research_backend_adapter("browser_use").provider == "browser_use"
