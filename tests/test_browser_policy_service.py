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


def test_download_policy_whitelist_and_bounded_dir(tmp_path):
    svc = BrowserPolicyService()
    c_white = _contract(download_policy="whitelist", download_allowlist=["example.com"])
    assert svc.enforce_download_policy(download_url="https://example.com/file", output_path=str(tmp_path / "a.txt"), contract=c_white).allow is True
    assert svc.enforce_download_policy(download_url="https://evil.com/file", output_path=str(tmp_path / "a.txt"), contract=c_white).allow is False

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    c_bound = _contract(download_policy="bounded_output_dir", output_dir=str(out_dir))
    assert svc.enforce_download_policy(download_url="https://example.com/f", output_path=str(out_dir / "ok.txt"), contract=c_bound).allow is True
    assert svc.enforce_download_policy(download_url="https://example.com/f", output_path=str(tmp_path / "outside.txt"), contract=c_bound).allow is False


def test_auth_policy_opt_in_required():
    svc = BrowserPolicyService()
    c = _contract(auth_policy="none")
    assert svc.enforce_auth_usage(requested=True, contract=c).allow is False
    c2 = _contract(auth_policy="explicit_opt_in")
    assert svc.enforce_auth_usage(requested=True, contract=c2).allow is True
