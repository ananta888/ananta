from __future__ import annotations

from agent.services.sandbox_policy_service import get_sandbox_policy_service


def test_sandbox_policy_normalize_defaults():
    svc = get_sandbox_policy_service()
    normalized = svc.normalize({})
    assert normalized["filesystem"]["enforce_workspace_boundary"] is True
    assert "/workspace" in normalized["filesystem"]["allowed_workspace_roots"]
    assert normalized["network"]["egress_mode"] == "restricted"
    assert normalized["command_wrappers"]["default_isolation_class"] == "bounded-mutable"


def test_sandbox_policy_command_requires_high_risk_class():
    svc = get_sandbox_policy_service()
    decision = svc.evaluate_command(
        command="sudo apt update",
        active_class="bounded-mutable",
        cfg={},
    )
    assert decision.allowed is False
    assert decision.required_class == "hardened-high-risk"
    assert decision.active_class == "bounded-mutable"
    assert decision.reason_code.startswith("sandbox_class_insufficient:")


def test_sandbox_policy_command_allows_with_hardened_class():
    svc = get_sandbox_policy_service()
    decision = svc.evaluate_command(
        command="docker run hello-world",
        active_class="hardened-high-risk",
        cfg={},
    )
    assert decision.allowed is True
    assert decision.reason_code == "sandbox_class_sufficient"
