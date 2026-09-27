"""Tool routing through /v1/decision: schema, strict response mapping, runtime guards, router path."""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from agent.services.tiny_router.parallel_decision import (
    NO_TOOL,
    TEXT_FIELD,
    CircuitBreaker,
    HttpParallelDecisionRuntime,
    ParallelDecisionAdapter,
    ParallelDecisionResponseError,
    build_tool_decision_schema,
    decision_to_payload,
    fixed_values,
)
from agent.services.tiny_router.profiles import ProfileCatalog
from agent.services.tiny_router.service import TinyToolRouterService
from agent.services.tiny_router.types import AdapterRequest, TinyActionModelProfile

pytestmark = pytest.mark.timeout(30)


def tool(name, properties=None, required=()):
    return {"type": "function", "function": {
        "name": name, "description": f"{name} tool",
        "parameters": {"type": "object", "properties": properties or {}, "required": list(required),
                       "additionalProperties": False}}}


RESTART = tool("service.restart", {
    "service": {"type": "string", "enum": ["hub", "worker"]},
    "force": {"type": "boolean"},
    "delay": {"type": "integer", "minimum": 0, "maximum": 5},
    "note": {"type": "string", "enum": ["a", "b"]},  # optional: not scored
}, required=("service", "force", "delay"))
SEARCH = tool("repo.search", {"query": {"type": "string"}}, required=("query",))
STATUS = tool("hub.status")
TOOLS = [RESTART, SEARCH, STATUS]


def profile(**overrides):
    row = {"profile_id": "decision", "model_id": "m", "tier": "decision", "adapter": "parallel_decision",
           "dialect": "openai", "license_id": "Apache-2.0", "source_url": "https://example.invalid",
           "commercial_use_allowed": True, "research_only": False, "supports_confidence": True,
           "supports_parallel_tools": False, "max_tools": 16, "context_window": 8192, "min_confidence": 0.8,
           "metadata": {"endpoint_env": "PD_URL"}}
    row.update(overrides)
    return TinyActionModelProfile.from_mapping(row)


def field(value, p=0.97, abstain=False):
    return {"value": value, "probability": p, "abstain": abstain}


def response(decision, **values):
    fields = {name: field(None, 0.5) for name in decision.schema}
    fields.update(values)
    return {"object": "decision", "results": [{"decision": {}, "fields": fields}]}


# --- schema --------------------------------------------------------------------------------------


def test_the_schema_has_a_tool_field_with_none_and_fixed_value_arguments_only():
    decision = build_tool_decision_schema(TOOLS)
    assert decision.schema["tool"]["choices"] == ["service.restart", "repo.search", "hub.status", NO_TOOL]
    assert decision.schema["service_restart__service"] == {
        "type": "enum", "choices": ["hub", "worker"],
        "description": "If service.restart is used: value of its argument 'service'."}
    assert decision.schema["service_restart__force"]["type"] == "boolean"
    assert decision.schema["service_restart__delay"]["type"] == "integer"
    assert "repo_search__query" not in decision.schema  # free text cannot be scored
    assert "service_restart__note" not in decision.schema  # optional: the tool's default applies
    assert decision.free_text_tools == {"repo.search"}
    assert decision.dependencies["tool"] == "independent"
    assert decision.dependencies["service_restart__force"] == "grouped:service.restart"


@pytest.mark.parametrize("spec, expected", [
    ({"enum": ["a", "b"]}, ("a", "b")),
    ({"type": "boolean"}, (True, False)),
    ({"type": "integer", "minimum": 0, "maximum": 2}, (0, 1, 2)),
    ({"type": "integer", "minimum": 0, "maximum": 1000}, None),  # more than 255 values
    ({"enum": ["a", "a"]}, None),  # duplicate candidates
    ({"enum": []}, None),
    ({"type": "string"}, None),
    ({"type": "number"}, None),
])
def test_fixed_values(spec, expected):
    assert fixed_values(spec) == expected


def test_a_schema_without_tools_is_refused():
    with pytest.raises(ValueError):
        build_tool_decision_schema([tool(NO_TOOL)])


# --- response mapping ------------------------------------------------------------------------------


@pytest.fixture
def decision():
    return build_tool_decision_schema(TOOLS)


def test_a_confident_tool_with_fixed_arguments_becomes_one_call(decision):
    payload = decision_to_payload(response(
        decision, tool=field("service.restart", 0.99), service_restart__service=field("worker", 0.95),
        service_restart__force=field(False, 0.9), service_restart__delay=field(0, 0.85),
    ), decision, min_confidence=0.8)
    assert payload == {"tool_calls": [{"name": "service.restart",
                                       "arguments": {"service": "worker", "force": False, "delay": 0},
                                       "confidence": 0.85}]}


@pytest.mark.parametrize("tool_field, reason", [
    (field("service.restart", 0.6), "tool_uncertain"),
    (field("service.restart", 0.95, abstain=True), "tool_uncertain"),
    (field("repo.search", 0.99), "free_text_arguments"),
])
def test_uncertain_or_free_text_tools_abstain(decision, tool_field, reason):
    payload = decision_to_payload(response(decision, tool=tool_field), decision, min_confidence=0.8)
    assert payload["tool_calls"] == [] and payload["reason"] == reason


def test_an_uncertain_argument_abstains_instead_of_guessing(decision):
    payload = decision_to_payload(response(
        decision, tool=field("service.restart", 0.99), service_restart__service=field("hub", 0.55),
        service_restart__force=field(False), service_restart__delay=field(0),
    ), decision, min_confidence=0.8)
    assert payload == {"tool_calls": [], "reason": "argument_uncertain", "tool_hint": "service.restart",
                       "confidence": 0.55}


def test_none_means_answer_without_a_tool(decision):
    assert decision_to_payload(response(decision, tool=field(NO_TOOL, 0.99)), decision,
                               min_confidence=0.8)["type"] == "respond"


def test_a_tool_without_arguments_is_a_call(decision):
    assert decision_to_payload(response(decision, tool=field("hub.status", 0.9)), decision, min_confidence=0.8) == {
        "tool_calls": [{"name": "hub.status", "arguments": {}, "confidence": 0.9}]}


@pytest.mark.parametrize("mutate, code", [
    (lambda r: r["results"][0]["fields"].__setitem__("tool", field("shell.exec", 0.99)), "tool_not_allowed"),
    (lambda r: r["results"][0]["fields"].pop("service_restart__force"), "fields_mismatch"),
    (lambda r: r["results"][0]["fields"].__setitem__("extra", field("x")), "fields_mismatch"),
    (lambda r: r["results"][0]["fields"].__setitem__("tool", field("hub.status", math.nan)), "probability_invalid"),
    (lambda r: r["results"][0]["fields"].__setitem__("tool", field("hub.status", 1.5)), "probability_invalid"),
    (lambda r: r["results"].append({}), "results_invalid"),
    (lambda r: r.__setitem__("object", "chat"), "response_invalid"),
])
def test_invalid_responses_are_rejected(decision, mutate, code):
    raw = response(decision, tool=field("hub.status"))
    mutate(raw)
    with pytest.raises(ParallelDecisionResponseError, match=code):
        decision_to_payload(raw, decision, min_confidence=0.8)


def test_an_argument_value_outside_its_set_is_rejected(decision):
    raw = response(decision, tool=field("service.restart"), service_restart__service=field("database"),
                   service_restart__force=field(True), service_restart__delay=field(1))
    with pytest.raises(ParallelDecisionResponseError, match="value_not_allowed"):
        decision_to_payload(raw, decision, min_confidence=0.8)


# --- adapter runtime guards ----------------------------------------------------------------------------


class Runtime:
    def __init__(self, answer=None, error=None, url="http://llama:8096"):
        self.answer, self.error, self.url, self.bodies = answer, error, url, []

    def base_url(self, profile):
        return self.url

    def decide(self, profile, body, *, timeout_ms):
        self.bodies.append((body, timeout_ms))
        if self.error:
            raise self.error
        sent = SimpleNamespace(schema=body["schema"])  # answer exactly the fields that were asked
        return self.answer(sent) if callable(self.answer) else self.answer


def test_the_request_never_reuses_a_context_across_requests():
    runtime = Runtime(lambda d: response(d, tool=field(NO_TOOL)))
    ParallelDecisionAdapter(runtime).propose(AdapterRequest("restart the worker", tuple(TOOLS), profile(), 900))
    body, timeout_ms = runtime.bodies[0]
    assert body["cache_context"] is False and body["contexts"] == ["restart the worker"] and timeout_ms == 900


def test_repeated_failures_open_the_circuit_and_it_recovers():
    now = [0.0]
    breaker = CircuitBreaker(threshold=2, cooldown=30, clock=lambda: now[0])
    adapter = ParallelDecisionAdapter(Runtime(error=OSError("down")), breaker=breaker)
    for _ in range(2):
        with pytest.raises(OSError):
            adapter.propose(AdapterRequest("x", tuple(TOOLS), profile(), 500))
    assert adapter.is_available(profile()) == (False, "parallel_decision_circuit_open")
    now[0] += 31
    assert adapter.is_available(profile())[0] is True


def test_without_an_endpoint_the_adapter_is_unavailable():
    runtime = HttpParallelDecisionRuntime(environ={})
    unavailable = (False, "parallel_decision_endpoint_unconfigured")
    assert ParallelDecisionAdapter(runtime).is_available(profile()) == unavailable
    assert HttpParallelDecisionRuntime(environ={"PD_URL": "file:///etc"}).base_url(profile()) == ""


# --- through the tiny router -------------------------------------------------------------------------


@dataclass
class Spec:
    risk_class: str


class Registry:
    RISK = {"service.restart": "write", "repo.search": "read", "hub.status": "read"}

    def get_tool(self, name):
        return Spec(self.RISK[name]) if name in self.RISK else None


class SchemaAdapter:
    def get_openai_tools(self, allowed):
        return [item for item in TOOLS if item["function"]["name"] in allowed]


def router(runtime):
    return TinyToolRouterService(catalog=ProfileCatalog.from_profiles([profile()]),
                                 adapters=[ParallelDecisionAdapter(runtime)], schema_adapter=SchemaAdapter(),
                                 registry=Registry())


CONFIG = {"mode": "active", "profile_order": ["decision"], "top_k": 5, "max_hops": 1, "max_total_ms": 2000,
          "allowed_risk_classes": ["read"], "commercial_use": True}


def test_a_confident_read_tool_becomes_the_router_candidate():
    decision = router(Runtime(lambda d: response(d, tool=field("hub.status", 0.97)))).route(
        prompt="how is the hub doing?", allowed_tools=["hub.status", "repo.search"], config=CONFIG)
    assert decision.status == "candidate" and decision.candidate.tool_name == "hub.status"
    assert decision.candidate.adapter_id == "parallel_decision"


def test_uncertainty_escalates_to_the_normal_tool_call():
    decision = router(Runtime(lambda d: response(d, tool=field("hub.status", 0.5)))).route(
        prompt="hmm", allowed_tools=["hub.status", "repo.search"], config=CONFIG)
    assert decision.status == "escalate" and decision.candidate is None


def test_a_write_tool_never_reaches_the_decision_in_read_only_scope():
    """Deterministic gates stay binding: the write tool is filtered before scoring."""
    runtime = Runtime(lambda d: response(d, tool=field("hub.status", 0.97)))
    router(runtime).route(prompt="restart the worker", allowed_tools=["service.restart", "hub.status"],
                          config=CONFIG, mutation_mode="read_only")
    schema = runtime.bodies[0][0]["schema"]
    assert schema["tool"]["choices"] == ["hub.status", NO_TOOL]


def test_an_endpoint_failure_escalates_instead_of_allowing():
    decision = router(Runtime(error=OSError("down"))).route(
        prompt="status", allowed_tools=["hub.status"], config=CONFIG)
    assert decision.status == "escalate" and decision.candidate is None


# --- one generated free-text argument (bounded open field) -----------------------------------------------


NOTE = tool("notes.write", {"title": {"type": "string"}, "body": {"type": "string"}}, required=("title", "body"))
MANIFEST = tool("layers.plan", {"manifest": {"type": "object"}}, required=("manifest",))


def text(value, truncated=False, generated=True):
    return {"value": value, "generated": generated, "tokens": 3, "truncated": truncated}


def test_the_open_field_fills_single_free_string_arguments_only():
    decision = build_tool_decision_schema([*TOOLS, NOTE, MANIFEST], open_field=True)
    assert decision.schema[TEXT_FIELD]["type"] == "string" and decision.schema[TEXT_FIELD]["max_tokens"] == 48
    assert "repo.search: its 'query'" in decision.schema[TEXT_FIELD]["description"]
    assert list(decision.schema)[-1] == TEXT_FIELD  # generated after the closed fields
    assert decision.text_argument_of("repo.search").argument == "query"
    assert decision.free_text_tools == {"notes.write", "layers.plan"}  # two strings / not a string
    assert decision.dependencies[TEXT_FIELD] == "conditional:closed_fields"
    assert decision.schema[TEXT_FIELD]["when"] == {"tool": ["repo.search"]}


def test_without_the_profile_flag_there_is_no_open_field():
    decision = build_tool_decision_schema(TOOLS)
    assert TEXT_FIELD not in decision.schema and decision.free_text_tools == {"repo.search"}


@pytest.fixture
def text_decision():
    return build_tool_decision_schema(TOOLS, open_field=True)


def test_a_generated_query_completes_the_call(text_decision):
    payload = decision_to_payload(response(
        text_decision, tool=field("repo.search", 0.96), text_argument=text("  CircuitBreaker "),
    ), text_decision, min_confidence=0.8)
    assert payload == {"tool_calls": [{"name": "repo.search", "arguments": {"query": "CircuitBreaker"},
                                       "confidence": 0.96}], "generated_arguments": ["query"]}


def test_a_tool_without_text_ignores_the_open_field(text_decision):
    payload = decision_to_payload(response(text_decision, tool=field("hub.status", 0.9), text_argument=text("")),
                                  text_decision, min_confidence=0.8)
    assert payload == {"tool_calls": [{"name": "hub.status", "arguments": {}, "confidence": 0.9}]}


@pytest.mark.parametrize("raw", [
    text(""), text("   "), text("x" * 201), text("a\nb"), text("Circuit", truncated=True), text(None),
    {**text("q"), "skipped": True},
])
def test_unusable_text_escalates(text_decision, raw):
    payload = decision_to_payload(response(text_decision, tool=field("repo.search"), text_argument=raw),
                                  text_decision, min_confidence=0.8)
    assert payload["tool_calls"] == [] and payload["reason"] == "text_argument_invalid"


def test_an_open_field_that_was_not_generated_is_rejected(text_decision):
    with pytest.raises(ParallelDecisionResponseError, match="open_field_invalid"):
        decision_to_payload(response(text_decision, tool=field("repo.search"), text_argument=field("q")),
                            text_decision, min_confidence=0.8)


def test_a_missing_open_field_is_rejected(text_decision):
    raw = response(text_decision, tool=field("repo.search"))
    raw["results"][0]["fields"].pop(TEXT_FIELD)
    with pytest.raises(ParallelDecisionResponseError, match="fields_mismatch"):
        decision_to_payload(raw, text_decision, min_confidence=0.8)


def test_a_generated_query_passes_the_router_validator():
    open_profile = profile(metadata={"endpoint_env": "PD_URL", "open_field": True})
    runtime = Runtime(lambda d: response(d, tool=field("repo.search", 0.97), text_argument=text("CircuitBreaker")))
    service = TinyToolRouterService(catalog=ProfileCatalog.from_profiles([open_profile]),
                                    adapters=[ParallelDecisionAdapter(runtime)], schema_adapter=SchemaAdapter(),
                                    registry=Registry())
    decision = service.route(prompt="where is the CircuitBreaker?", allowed_tools=["repo.search", "hub.status"],
                             config=CONFIG)
    assert decision.status == "candidate" and decision.candidate.tool_name == "repo.search"
    assert dict(decision.candidate.arguments) == {"query": "CircuitBreaker"}
    assert runtime.bodies[0][0]["schema"][TEXT_FIELD]["type"] == "string"


# --- Hub MCP tools declare nothing as required -----------------------------------------------------------

GREP = tool("repo.grep", {"pattern": {"type": "string"}, "limit": {"type": "integer"}})
FILES = tool("codecompass.get_file_context", {"paths": {"type": "array"}, "reason": {"type": "string"}})
INTEL = tool("codecompass.architecture_intelligence", {"snapshot_ref": {"type": "string"}})


def test_a_free_leading_parameter_becomes_an_optional_text_argument():
    decision = build_tool_decision_schema([GREP, FILES, INTEL, STATUS], open_field=True)
    assert [(item.tool, item.argument, item.required) for item in decision.text_arguments] == [
        ("repo.grep", "pattern", False), ("codecompass.architecture_intelligence", "snapshot_ref", False)]
    assert decision.free_text_tools == {"codecompass.get_file_context"}  # leading list: left to the model
    assert "repo.grep: its 'pattern', optional" in decision.schema[TEXT_FIELD]["description"]
    # without the open field a tool with a free leading parameter is not called empty
    assert build_tool_decision_schema([GREP, STATUS]).free_text_tools == {"repo.grep"}


def test_an_empty_optional_text_calls_the_tool_without_it():
    decision = build_tool_decision_schema([GREP, INTEL], open_field=True)
    grep = decision_to_payload(response(decision, tool=field("repo.grep"), text_argument=text("CircuitBreaker")),
                               decision, min_confidence=0.8)
    assert grep["tool_calls"][0]["arguments"] == {"pattern": "CircuitBreaker"}
    intel = decision_to_payload(response(decision, tool=field("codecompass.architecture_intelligence"),
                                         text_argument=text("")), decision, min_confidence=0.8)
    assert intel == {"tool_calls": [{"name": "codecompass.architecture_intelligence", "arguments": {},
                                     "confidence": 0.97}]}
