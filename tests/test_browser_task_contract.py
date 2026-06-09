from __future__ import annotations

import pytest

from agent.services.browser_task_contract import BrowserTaskContract


def test_contract_defaults_and_validation():
    c = BrowserTaskContract.from_payload({"allowed_domains": ["example.com"]})
    assert c.max_actions == 10
    assert c.timeout_seconds == 120


def test_contract_invalid_policy_rejected():
    with pytest.raises(ValueError):
        BrowserTaskContract.from_payload({"allowed_domains": ["example.com"], "download_policy": "invalid"})


def test_contract_persist_session_default_false():
    c = BrowserTaskContract.from_payload({"allowed_domains": ["example.com"]})
    assert c.persist_session is False


def test_contract_persist_session_can_be_set():
    c = BrowserTaskContract.from_payload({"allowed_domains": ["example.com"], "persist_session": True})
    assert c.persist_session is True


def test_contract_blocked_domains_default():
    from agent.services.browser_task_contract import _DEFAULT_BLOCKED_DOMAINS
    c = BrowserTaskContract.from_payload({"allowed_domains": ["example.com"]})
    assert c.blocked_domains == _DEFAULT_BLOCKED_DOMAINS
    assert "localhost" in c.blocked_domains
    assert "169.254.169.254" in c.blocked_domains


def test_contract_blocked_domains_custom():
    c = BrowserTaskContract.from_payload({
        "allowed_domains": ["example.com"],
        "blocked_domains": ["evil.com", "bad.net"],
    })
    assert "evil.com" in c.blocked_domains
    assert "bad.net" in c.blocked_domains
