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


def test_the_answer_is_recovered_from_a_final_answer_execution_report():
    report = "Tool 'final_answer': Erfolg\nOutput: {'answer': '## Zwischenstand\\n- Hub orchestriert\\n- \\'Worker\\' führen aus'}"
    task = SimpleNamespace(last_output=report, last_proposal={"tool_calls": None})

    assert step_result(task) == "## Zwischenstand\n- Hub orchestriert\n- 'Worker' führen aus"
    assert step_result(SimpleNamespace(last_output="Tool 'final_answer': Erfolg\nOutput: kaputt")) \
        == "Tool 'final_answer': Erfolg\nOutput: kaputt"


def test_the_full_answer_comes_from_the_decision_when_the_proposal_lost_its_tool_calls():
    answer = "## Zwischenstand\n" + "- Punkt\n" * 400  # longer than the 2000-character output cut
    task = SimpleNamespace(last_output="Tool 'final_answer': Erfolg\nOutput: {'answer': '## Zw…\n[truncated to 2000 chars]",
                           last_proposal={"tool_calls": None},
                           history=[{"event_type": "autopilot_decision",
                                     "tool_calls": [{"name": "final_answer", "args": {"answer": answer}}]}])

    assert step_result(task) == answer.strip()


def test_hub_sized_steps_skip_the_propose_context_compactor():
    from agent.services._task_scoped_propose_orch import _is_long_context_step

    assert _is_long_context_step({"status_reason_details": {"long_context": {"role": "step", "kind": "chunk"}}})
    assert not _is_long_context_step({"status_reason_details": {"long_context": {"role": "parent"}}})
    assert not _is_long_context_step({"status_reason_details": None})
    assert not _is_long_context_step({})
