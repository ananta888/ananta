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
