from __future__ import annotations

from agent.services.config_graph_approval_service import ConfigGraphApprovalService


def test_approval_token_is_bound_to_ops_and_risk() -> None:
    ops = [{
        "op": "set_data",
        "target": "path_rule::docs/**",
        "data": {"blocked_ai_modes": ["full_llm"]},
    }]
    service = ConfigGraphApprovalService(secret="test-secret")
    token = service.expected_token(ops=ops, risk_tier="high")

    assert token
    assert service.validate(
        ops=ops,
        risk_tier="high",
        approval_token=token,
    ).approved is True
    assert service.validate(
        ops=ops,
        risk_tier="critical",
        approval_token=token,
    ).approved is False


def test_approval_fails_without_server_secret() -> None:
    service = ConfigGraphApprovalService(secret="")
    decision = service.validate(ops=[], risk_tier="high", approval_token="anything")

    assert decision.approved is False
    assert decision.reason_code == "approval_secret_not_configured"
