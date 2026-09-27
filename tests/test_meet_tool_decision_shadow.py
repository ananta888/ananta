"""Companion tool-decision shadow: records a decision next to what the reply did, without question text."""

import json

import pytest

from ananta_contracts.tool_decision import CALL, RESPOND, ToolDecision
from worker.meet_media import tool_decision_shadow as shadow
from worker.meet_media.tool_decision import DecisionOutcome

pytestmark = pytest.mark.timeout(30)
QUESTION = "Welche Index-Layer gibt es?"


class Sink:
    def __init__(self):
        self.records = []

    def write(self, record):
        self.records.append(record)


class Decider:
    def __init__(self, outcome):
        self.outcome, self.asked = outcome, []

    def decide(self, question, definitions):
        self.asked.append(question)
        return self.outcome


def record(outcome, calls=(), mode="shadow"):
    sink = Sink()
    result = shadow.ToolDecisionShadow(Decider(outcome), sink, wall=lambda: 1000.0).record(
        QUESTION, outcome, route="ananta_code_architecture", calls=list(calls), mode=mode)
    assert sink.records == [result]
    return result


def test_a_decision_is_recorded_next_to_the_calls_without_question_text():
    outcome = DecisionOutcome(ToolDecision(CALL, "codecompass_layers_heads", {}, 0.97, margin=0.9), "", 160.0)
    calls = [{"query": "layer", "forced": True}, {"tool": "codecompass_layers_heads", "query": ""}]
    result = record(outcome, calls=calls)
    assert result["decision"] == "codecompass_layers_heads" and result["status"] == CALL
    assert result["forced"] == ["codecompass_search"] and result["model_first"] == "codecompass_layers_heads"
    assert result["actual_first"] == "codecompass_search" and result["agrees"] is False
    assert result["decision_ms"] == 160.0 and result["question_chars"] == len(QUESTION)
    assert "Index-Layer" not in json.dumps(result)


def test_a_respond_decision_counts_as_none():
    result = record(DecisionOutcome(ToolDecision(RESPOND, confidence=0.99, reason="no_tool_needed"), "", 150.0))
    assert result["decision"] == "none" and result["agrees"] is True and result["argument_chars"] == 0


def test_the_generated_argument_is_compared_without_keeping_its_text():
    decision = ToolDecision(CALL, "codecompass_search", {"query": " circuitbreaker "}, 0.97,
                            generated_arguments=("query",))
    result = record(DecisionOutcome(decision, "", 200.0), calls=[{"query": "CircuitBreaker", "forced": True}],
                    mode="fast")
    assert result["mode"] == "fast" and result["argument_equal"] is True and result["argument_chars"] == 16
    assert "ircuit" not in json.dumps(result)


def test_an_error_outcome_is_recorded():
    result = record(DecisionOutcome(None, "meet_llm_transport_failed", 4000.0))
    assert result["decision"] is None and result["error"] == "meet_llm_transport_failed"


def test_observe_decides_now_and_observe_later_does_not_block():
    outcome = DecisionOutcome(ToolDecision(RESPOND, confidence=0.99), "", 1.0)
    decider, sink = Decider(outcome), Sink()
    calls = [{"query": "x"}]
    worker = shadow.ToolDecisionShadow(decider, sink).observe_later(QUESTION, [], route="r", calls=calls)
    calls.clear()
    worker.join(5)
    assert decider.asked == [QUESTION] and sink.records[0]["model_first"] == "codecompass_search"


def test_from_env_shares_the_decider_and_is_off_without_one(tmp_path):
    assert shadow.from_env({}) is None
    env = {"MEET_TOOL_DECISION_URL": "http://h:1", shadow.LOG_ENV: str(tmp_path / "s.jsonl")}
    assert isinstance(shadow.from_env(env), shadow.ToolDecisionShadow)
    given = Decider(None)
    assert shadow.from_env({shadow.LOG_ENV: str(tmp_path / "s.jsonl")}, decider=given)._decider is given


def test_the_sink_appends_lines_and_swallows_write_errors(tmp_path):
    target = tmp_path / "s.jsonl"
    sink = shadow.JsonLinesSink(str(target))
    sink.write({"a": 1})
    sink.write({"a": 2})
    assert [json.loads(line)["a"] for line in target.read_text().splitlines()] == [1, 2]
    shadow.JsonLinesSink(str(tmp_path / "missing" / "s.jsonl")).write({"a": 3})
