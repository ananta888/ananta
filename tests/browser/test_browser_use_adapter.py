from __future__ import annotations

from agent.services.browser_task_contract import BrowserTaskContract
from agent.services.browser_use_adapter import BrowserUseExecutionAdapter


def _contract():
    return BrowserTaskContract.from_payload({
        "allowed_domains": ["example.com"],
        "max_actions": 3,
        "timeout_seconds": 10,
        "download_policy": "deny",
        "auth_policy": "none",
        "screenshot_policy": "none",
    })


def test_adapter_blocks_disallowed_domain():
    a = BrowserUseExecutionAdapter()
    r = a.execute(start_url="https://evil.com", actions=[], contract=_contract())
    assert r.status == "blocked"


def test_adapter_produces_normalized_trace():
    a = BrowserUseExecutionAdapter()
    r = a.execute(
        start_url="https://example.com",
        actions=[{"type": "click", "target": "#x"}],
        contract=_contract(),
        action_executor=lambda _a: {"ok": True, "output": None},
    )
    assert r.status == "success"
    assert r.trace[0]["action_type"] == "click"


def test_adapter_preflight_ready_and_unready():
    a = BrowserUseExecutionAdapter()
    off = a.preflight({"enabled": False, "command": "bash -lc true"})
    assert off.ready is False
    missing = a.preflight({"enabled": True, "command": ""})
    assert missing.ready is False
    ok = a.preflight({"enabled": True, "command": "bash -lc true"})
    assert ok.ready is True


def test_adapter_blocks_download_outside_policy():
    a = BrowserUseExecutionAdapter()
    c = BrowserTaskContract.from_payload(
        {
            "allowed_domains": ["example.com"],
            "max_actions": 3,
            "timeout_seconds": 10,
            "download_policy": "bounded_output_dir",
            "output_dir": "/tmp/ok",
            "auth_policy": "none",
            "screenshot_policy": "none",
        }
    )
    r = a.execute(
        start_url="https://example.com",
        actions=[{"type": "download", "url": "https://example.com/file", "output_path": "/tmp/out/file"}],
        contract=c,
    )
    assert r.status == "blocked"
    assert r.failure_class == "policy_denied"
