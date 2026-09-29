from agent.services.execution_risk_policy_service import evaluate_execution_risk
from agent.runtime_policy import review_policy


def test_execution_risk_denies_scoped_terminal_without_terminal_capability():
    decision = evaluate_execution_risk(
        command="rm -rf /tmp/test",
        tool_calls=None,
        task={"worker_execution_context": {"context_policy": {}}, "required_capabilities": []},
        agent_cfg={
            "execution_risk_policy": {
                "enabled": True,
                "default_action": "deny",
                "task_scoped_only": True,
                "require_terminal_capability_for_command": True,
                "deny_risk_levels": ["high", "critical"],
            }
        },
    )
    assert decision.allowed is False
    assert "terminal_capability_required" in decision.reasons
    assert decision.risk_level in {"high", "critical"}


def test_execution_risk_allows_scoped_terminal_when_capability_explicit():
    decision = evaluate_execution_risk(
        command="echo ok",
        tool_calls=None,
        task={"worker_execution_context": {"kind": "worker_execution_context"}, "required_capabilities": ["terminal"]},
        agent_cfg={"execution_risk_policy": {"enabled": True, "task_scoped_only": True}},
    )
    assert decision.allowed is True
    assert decision.risk_level in {"medium", "high"}


def test_review_policy_requires_review_for_terminal_risk():
    policy = review_policy(
        {
            "review_policy": {
                "enabled": True,
                "min_risk_level_for_review": "high",
                "terminal_risk_level": "high",
            }
        },
        backend="sgpt",
        task_kind="coding",
        risk_level="medium",
        uses_terminal=True,
        uses_file_access=False,
    )
    assert policy["required"] is True
    assert policy["reason"].startswith("risk_level_review_required:")
    assert policy["risk_level"] == "high"
