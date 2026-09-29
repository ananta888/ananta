from __future__ import annotations

from types import SimpleNamespace

from agent.services.workflow_policy_adapter import decide_workflow_policy


def test_policy_allows_safe_read() -> None:
    d = SimpleNamespace(capability="read", risk_class="low", approval_required=False)
    out = decide_workflow_policy(d)
    assert out["decision"] == "allow"


def test_policy_requires_approval_for_write() -> None:
    d = SimpleNamespace(capability="write", risk_class="medium", approval_required=True)
    out = decide_workflow_policy(d, approved=False)
    assert out["decision"] == "confirm_required"


def test_policy_allows_after_approval() -> None:
    d = SimpleNamespace(capability="admin", risk_class="critical", approval_required=True)
    out = decide_workflow_policy(d, approved=True)
    assert out["decision"] == "allow"
