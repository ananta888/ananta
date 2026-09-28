from agent.tool_guardrails import evaluate_tool_call_guardrails


def test_final_answer_returns_the_answer_without_side_effects():
    from agent.tools_answer import final_answer_tool

    assert final_answer_tool(answer=" ## Ergebnis\n- a ") == {"answer": "## Ergebnis\n- a"}
    assert "error" in final_answer_tool(answer="  ")


def test_final_answer_is_registered_and_passes_a_read_only_level():
    import agent.tools  # noqa: F401 -- registers the action pack
    from agent.tools_registry import registry

    assert registry.execute("final_answer", {"answer": "x"}).success
    decision = evaluate_tool_call_guardrails(
        [{"name": "final_answer", "args": {"answer": "x"}}],
        {"llm_tool_guardrails": {"enabled": True, "blocked_classes": ["write", "admin"]}})
    assert decision.allowed
