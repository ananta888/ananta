from __future__ import annotations

from agent.services.browser_policy_service import BrowserPolicyService
from agent.services.browser_task_contract import BrowserTaskContract


def _contract(**overrides):
    base = {
        "allowed_domains": ["example.com"],
        "max_actions": 2,
        "timeout_seconds": 10,
        "download_policy": "deny",
        "auth_policy": "none",
        "screenshot_policy": "none",
    }
    base.update(overrides)
    return BrowserTaskContract.from_payload(base)


def test_domain_allow_and_deny():
    svc = BrowserPolicyService()
    assert svc.enforce_domain(url="https://example.com/a", contract=_contract()).allow is True
    assert svc.enforce_domain(url="https://evil.com", contract=_contract()).allow is False


def test_action_budget_enforced():
    svc = BrowserPolicyService()
    assert svc.enforce_action_budget(action_count=2, contract=_contract()).allow is True
    assert svc.enforce_action_budget(action_count=3, contract=_contract()).allow is False
