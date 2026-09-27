"""Companion decider: one /v1/decision call through the shared contract, the fast-path cut, settings."""

import math

import pytest

from ananta_contracts.tool_decision import ABSTAIN, CALL, RESPOND
from worker.meet_media import tool_decision

pytestmark = pytest.mark.timeout(30)

SEARCH = {"type": "function", "function": {"name": "codecompass_search", "description": "search code",
                                           "parameters": {"type": "object", "properties": {"query": {"type": "string"}},
                                                          "required": ["query"]}}}
HEADS = {"type": "function", "function": {"name": "codecompass_layers_heads", "description": "index layers",
                                          "parameters": {"type": "object", "properties": {}}}}
TOOLS = [SEARCH, HEADS]


def answer(tool, p=0.95, text=None, skipped=False):
    fields = {"tool": {"value": tool, "probability": p, "margin": 0.9}}
    fields["text_argument"] = {"value": text or "", "generated": not skipped, "skipped": skipped, "tokens": 2,
                               "truncated": False}
    return {"object": "decision", "results": [{"fields": fields}]}


class Client:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.requests = response, error, []

    def exchange(self, path, payload, budget):
        self.requests.append((path, payload, budget))
        if self.error:
            raise self.error
        return self.response


def decide(response=None, error=None, open_field=True):
    client = Client(response, error)
    decider = tool_decision.ToolDecider(client, model="m", open_field=open_field, clock=iter([1.0, 1.2]).__next__)
    return decider.decide("Wo ist der CircuitBreaker?", TOOLS), client


def test_a_decision_goes_through_the_shared_contract():
    outcome, client = decide(answer("codecompass_search", text="CircuitBreaker"))
    assert outcome.decision.status == CALL and dict(outcome.decision.arguments) == {"query": "CircuitBreaker"}
    assert outcome.error == "" and outcome.ms == 200.0
    path, body, budget = client.requests[0]
    assert path == "/v1/decision" and body["cache_context"] is False and budget == tool_decision.BUDGET_SECONDS
    assert body["schema"]["text_argument"]["when"] == {"tool": ["codecompass_search"]}


def test_without_the_open_field_a_text_tool_abstains():
    outcome, client = decide({"object": "decision", "results": [{"fields": {
        "tool": {"value": "codecompass_search", "probability": 0.95}}}]}, open_field=False)
    assert "text_argument" not in client.requests[0][1]["schema"]
    assert outcome.decision.status == ABSTAIN and outcome.decision.reason == "free_text_arguments"


@pytest.mark.parametrize("response, error", [
    ({"object": "decision", "results": []}, None),
    (answer("shell_exec"), None),
    (answer("none", math.nan), None),
    (None, ValueError("meet_llm_transport_failed")),
])
def test_failures_are_outcomes_not_exceptions(response, error):
    outcome, _client = decide(response, error)
    assert outcome.decision is None and outcome.error.startswith(("tool_decision_", "meet_llm_"))


class Decider:
    def __init__(self, outcome):
        self.outcome = outcome

    def decide(self, question, definitions):
        return self.outcome


@pytest.mark.parametrize("response, passed", [
    (answer("codecompass_search", 0.93, "CircuitBreaker"), True),
    (answer("codecompass_search", 0.80, "CircuitBreaker"), False),  # below the calibrated 0.85
    (answer("none", 0.99, skipped=True), False),                    # respond: the model answers
    (answer("codecompass_layers_heads", 0.97, skipped=True), True),
])
def test_the_fast_path_passes_on_confident_calls_only(response, passed):
    outcome, _client = decide(response)
    fast = tool_decision.FastToolChoice(Decider(outcome))
    assert (fast("q", TOOLS) is not None) is passed and fast.last is outcome
    if outcome.decision.status == RESPOND:
        assert fast("q", TOOLS) is None


def test_settings(tmp_path):
    assert tool_decision.decider_from_env({}) is None
    assert tool_decision.decider_from_env({tool_decision.URL_ENV: "file:///etc"}) is None
    decider = tool_decision.decider_from_env({tool_decision.LEGACY_URL_ENV: "http://h:1"})
    assert isinstance(decider, tool_decision.ToolDecider) and decider._open_field is True
    assert tool_decision.decider_from_env({tool_decision.URL_ENV: "http://h:1",
                                           tool_decision.LEGACY_ARGUMENT_ENV: "off"})._open_field is False
    assert tool_decision.fast_choice_from_env(decider, {}) is None  # off by default
    assert tool_decision.fast_choice_from_env(None, {tool_decision.FAST_ENV: "1"}) is None
    fast = tool_decision.fast_choice_from_env(decider, {tool_decision.FAST_ENV: "1"})
    assert fast._min_confidence == 0.85
    # operators may raise the threshold, not lower it below the calibrated value
    def threshold(value):
        env = {tool_decision.FAST_ENV: "1", tool_decision.MIN_CONFIDENCE_ENV: value}
        return tool_decision.fast_choice_from_env(decider, env)._min_confidence

    assert threshold("0.5") == 0.85 and threshold("0.95") == 0.95 and threshold("x") == 0.85
