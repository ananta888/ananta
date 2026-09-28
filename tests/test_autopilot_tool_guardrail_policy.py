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


def _safe(tool_calls):
    return AutopilotDecisionService().evaluate_tool_guardrails_for_autopilot(
        task=_task(), policy={"level": "safe", "allowed_tool_classes": ["read"]},
        agent_cfg={"llm_tool_guardrails": {"enabled": True, "max_external_calls_per_request": 99,
                                           "max_estimated_cost_units_per_request": 999,
                                           "tool_classes": {"file_read": "read", "file_write": "write"}}},
        reason="r", command=None, tool_calls=tool_calls)


def test_safe_level_judges_the_resolved_call_not_the_invented_name():
    decision = _safe([{"name": "task_complete", "args": {"task_id": "t", "answer": "x"}}])  # resolves to file_write
    assert decision.allowed is False
    assert "guardrail_class_blocked:write" in decision.reasons


def test_safe_level_blocks_unknown_but_allows_reads_and_final_answer():
    assert _safe([{"name": "repo.read_file_range", "args": {"file": "a.md"}}]).allowed  # resolves to file_read
    assert _safe([{"name": "final_answer", "args": {"answer": "x"}}]).allowed
    unresolved = _safe([{"name": "mystery", "args": {"command": "ls"}}])
    assert unresolved.allowed is False and "guardrail_class_blocked:unknown" in unresolved.reasons
