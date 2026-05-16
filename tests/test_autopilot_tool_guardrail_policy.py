from types import SimpleNamespace

from agent.services.autopilot_decision_service import AutopilotDecisionService


def _task():
    return SimpleNamespace(description="d", history=[])


def test_autopilot_tool_guardrail_blocks_tokens_in_balanced_mode():
    svc = AutopilotDecisionService()
    decision = svc.evaluate_tool_guardrails_for_autopilot(
        task=_task(),
        policy={"level": "balanced", "allowed_tool_classes": ["read", "write"]},
        agent_cfg={
            "llm_tool_guardrails": {
                "enabled": True,
                "max_tokens_per_request": 1,
                "max_external_calls_per_request": 99,
                "max_estimated_cost_units_per_request": 999,
                "tool_classes": {"file_read": "read"},
            }
        },
        reason="r",
        command=None,
        tool_calls=[{"name": "file_read", "args": {"path": "/tmp/x"}}],
    )
    assert decision is not None
    assert decision.allowed is False
    assert "guardrail_max_estimated_tokens_exceeded" in decision.reasons


def test_autopilot_tool_guardrail_ignores_token_cap_in_aggressive_mode():
    svc = AutopilotDecisionService()
    decision = svc.evaluate_tool_guardrails_for_autopilot(
        task=_task(),
        policy={"level": "aggressive", "allowed_tool_classes": ["read", "write", "admin", "unknown"]},
        agent_cfg={
            "llm_tool_guardrails": {
                "enabled": True,
                "max_tokens_per_request": 1,
                "max_external_calls_per_request": 99,
                "max_estimated_cost_units_per_request": 999,
                "tool_classes": {"file_read": "read"},
            }
        },
        reason="r",
        command=None,
        tool_calls=[{"name": "file_read", "args": {"path": "/tmp/x"}}],
    )
    assert decision is not None
    assert decision.allowed is True
    assert "guardrail_max_estimated_tokens_exceeded" not in decision.reasons
