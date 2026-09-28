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


def test_a_long_final_answer_reaches_last_output_uncut(client, app, admin_auth_header):
    """The answer is the task's result, not tool noise: the 2000-char tool-output compaction must not cut it."""
    from agent.routes.tasks.utils import _update_local_task_status

    answer = "## Ergebnis\n" + "\n".join(f"- Punkt {i}: " + "inhalt " * 12 for i in range(120))  # ~11k chars
    with app.app_context():
        _update_local_task_status("fa-long", "assigned", title="Analyse", description="d", task_kind="analysis")
    response = client.post("/tasks/fa-long/step/execute",
                           json={"tool_calls": [{"name": "final_answer", "args": {"answer": answer}}]},
                           headers=admin_auth_header)
    assert response.status_code == 200, response.get_data(as_text=True)
    with app.app_context():
        from agent.repository import task_repo

        output = task_repo.get_by_id("fa-long").last_output or ""
    assert answer.splitlines()[-1].strip() in output and "[truncated" not in output
