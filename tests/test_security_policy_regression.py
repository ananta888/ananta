from agent.runtime_policy import evaluate_trigger_precheck, review_policy
from agent.tool_guardrails import evaluate_tool_call_guardrails


def test_trigger_precheck_denies_high_risk_sources_when_configured() -> None:
    cfg = {
        "trigger_policy": {
            "enabled": True,
            "deny_high_risk": True,
            "source_risk_map": {"github": "high"},
        }
    }
    decision = evaluate_trigger_precheck(
        cfg,
        source="github",
        payload={"event": "push"},
        parsed_tasks=[{"title": "Investigate failing CI"}],
    )
    assert decision["allowed"] is False
    assert decision["decision"] == "blocked"
    assert decision["reason"] == "high_risk_source_denied"


def test_trigger_precheck_applies_allowlist_as_default_deny_for_unknown_sources() -> None:
    cfg = {
        "trigger_policy": {
            "enabled": True,
            "allowed_sources": ["email"],
        }
    }
    decision = evaluate_trigger_precheck(
        cfg,
        source="slack",
        payload={"event": "message"},
        parsed_tasks=[{"title": "Review incident summary"}],
    )
    assert decision["allowed"] is False
    assert decision["reason"] == "source_not_allowed"


def test_review_policy_requires_explicit_review_for_research_backends() -> None:
    cfg = {
        "review_policy": {
            "enabled": True,
            "research_backends": ["deerflow"],
            "task_kinds": ["research"],
        }
    }
    policy = review_policy(cfg, backend="deerflow", task_kind="research")
    assert policy["enabled"] is True
    assert policy["required"] is True
    assert policy["reason"] == "research_backend_review_required"


def test_tool_guardrails_block_blocked_classes_in_risky_modes() -> None:
    cfg = {
        "llm_tool_guardrails": {
            "enabled": True,
            "blocked_classes": ["write", "admin"],
            "tool_classes": {"create_team": "write"},
        }
    }
    decision = evaluate_tool_call_guardrails(
        [{"name": "create_team", "args": {"name": "SecTeam", "team_type": "Scrum"}}],
        cfg,
    )
    assert decision.allowed is False
    assert "guardrail_class_blocked:write" in decision.reasons
