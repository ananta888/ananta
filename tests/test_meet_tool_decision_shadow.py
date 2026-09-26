"""Companion tool-decision shadow: schema, strict reading, recording, never in the reply path."""

import json
import math

import pytest

from worker.meet_media import tool_decision_shadow as shadow

pytestmark = pytest.mark.timeout(30)


def tool(name, description="d"):
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {}}}


TOOLS = [tool("codecompass_search", "search code"), tool("codecompass_layers_heads", "index layers")]


def answer(value, probability=0.93, margin=0.8):
    return {"object": "decision", "results": [{"fields": {"tool": {
        "value": value, "probability": probability, "margin": margin}}}]}


class Client:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.requests = response, error, []

    def exchange(self, path, payload, budget):
        self.requests.append((path, payload, budget))
        if self.error:
            raise self.error
        return self.response


class Sink:
    def __init__(self):
        self.records = []

    def write(self, record):
        self.records.append(record)


def observe(client, calls=(), question="Welche Index-Layer gibt es?"):
    sink = Sink()
    record = shadow.ToolDecisionShadow(client, sink, clock=iter([1.0, 1.25]).__next__, wall=lambda: 1000.0) \
        .observe(question, TOOLS, route="ananta_code_architecture", calls=list(calls))
    assert sink.records == [record]
    return record


def test_the_schema_offers_the_tools_and_none():
    schema = shadow.decision_schema(TOOLS + [tool("codecompass_search"), tool("none")])
    assert schema["tool"]["choices"] == ["codecompass_search", "codecompass_layers_heads", "none"]
    assert "codecompass_layers_heads: index layers" in schema["tool"]["description"]
    with pytest.raises(ValueError):
        shadow.decision_schema([])


def test_a_decision_is_recorded_next_to_the_model_call_without_question_text():
    client = Client(answer("codecompass_layers_heads"))
    record = observe(client, calls=[{"query": "layer", "forced": True},
                                    {"tool": "codecompass_layers_heads", "query": ""}])
    assert record["decision"] == "codecompass_layers_heads"
    assert record["forced"] == ["codecompass_search"] and record["model_first"] == "codecompass_layers_heads"
    assert record["actual_first"] == "codecompass_search" and record["agrees"] is False
    assert record["decision_ms"] == 250.0 and record["question_chars"] == 27
    assert "Index-Layer" not in json.dumps(record)
    path, payload, _budget = client.requests[0]
    assert path == "/v1/decision" and payload["cache_context"] is False
    assert payload["contexts"] == ["Welche Index-Layer gibt es?"]


def test_no_model_call_counts_as_none():
    record = observe(Client(answer("none")))
    assert record["model_first"] == record["actual_first"] == "none" and record["agrees"] is True


@pytest.mark.parametrize("response", [
    answer("shell_exec"), answer("none", math.nan), answer("none", 1.5), answer("none", True),
    {"results": []}, {"results": [{}, {}]}, [],
])
def test_malformed_answers_are_recorded_as_errors(response):
    record = observe(Client(response))
    assert record["decision"] is None and record["error"].startswith("tool_decision_")


def test_a_transport_failure_is_recorded_not_raised():
    record = observe(Client(error=ValueError("meet_llm_transport_failed")))
    assert record == {**record, "decision": None, "error": "meet_llm_transport_failed"}


def test_observe_later_does_not_block_and_copies_its_inputs():
    client, sink = Client(answer("codecompass_search")), Sink()
    calls = [{"query": "x"}]
    worker = shadow.ToolDecisionShadow(client, sink).observe_later("q", TOOLS, route="r", calls=calls)
    calls.clear()
    worker.join(5)
    assert sink.records[0]["model_first"] == "codecompass_search"


def test_from_env_is_off_without_a_valid_url(tmp_path):
    assert shadow.from_env({}) is None
    assert shadow.from_env({shadow.URL_ENV: "file:///etc/passwd"}) is None
    assert shadow.from_env({shadow.URL_ENV: "http://user:pw@host:1"}) is None
    configured = shadow.from_env({shadow.URL_ENV: "http://192.168.32.1:18150",
                                  shadow.LOG_ENV: str(tmp_path / "s.jsonl")})
    assert isinstance(configured, shadow.ToolDecisionShadow)


def test_the_sink_appends_lines_and_swallows_write_errors(tmp_path):
    target = tmp_path / "s.jsonl"
    sink = shadow.JsonLinesSink(str(target))
    sink.write({"a": 1})
    sink.write({"a": 2})
    assert [json.loads(line)["a"] for line in target.read_text().splitlines()] == [1, 2]
    shadow.JsonLinesSink(str(tmp_path / "missing" / "s.jsonl")).write({"a": 3})
