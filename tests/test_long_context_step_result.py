import json
from types import SimpleNamespace

from agent.services.long_context_step_result import proposal_text, step_result


def test_answer_in_a_result_call_is_the_step_result():
    task = SimpleNamespace(last_output="[tool_guardrail] blocked", last_proposal={"tool_calls": [
        {"name": "task_context", "args": {"chunk": "1 of 5", "summary": ["Hub orchestriert", "Worker führen aus"]}}]})

    assert step_result(task) == "- Hub orchestriert\n- Worker führen aus"


def test_file_write_content_beats_the_write_report():
    proposal = {"tool_calls": [{"name": "file_write", "args": {"path": "summary.md", "content": "# Übersicht\nA"}}]}
    task = SimpleNamespace(last_output="Tool 'file_write': Erfolg", last_proposal=json.dumps(proposal))

    assert step_result(task) == "# Übersicht\nA"


def test_last_output_when_the_proposal_carries_no_text():
    task = SimpleNamespace(last_output="Ergebnis", last_proposal={"command": "echo x", "tool_calls": [
        {"name": "file_read", "args": {"path": "a.md"}}]})

    assert proposal_text(task) == ""
    assert step_result(task) == "Ergebnis"
    assert step_result(SimpleNamespace(last_output=None)) == ""
