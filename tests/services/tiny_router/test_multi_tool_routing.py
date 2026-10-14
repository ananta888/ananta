"""PDT-003: multi-tool planning through the tiny router (additive ``plan`` path).

Headless: the decision runtime is a fake; no network, no model.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from agent.services.tiny_router.multi_tool_adapter import MultiToolPlanAdapter, registry_argument_validator
from agent.services.tiny_router.profiles import ProfileCatalog
from agent.services.tiny_router.service import TinyToolRouterService
from agent.services.tiny_router.types import AdapterRequest, TinyActionModelProfile

pytestmark = pytest.mark.timeout(30)


def tool(name, properties=None, required=()):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"{name} tool",
            "parameters": {"type": "object", "properties": properties or {}, "required": list(required)},
        },
    }


WEATHER = tool("get_weather", {"unit": {"type": "string", "enum": ["celsius", "fahrenheit"]}}, required=("unit",))
TICKET = tool("create_ticket", {"priority": {"type": "string", "enum": ["low", "high"]}}, required=("priority",))
STATUS = tool("hub.status")
SEARCH = tool("repo.search", {"query": {"type": "string"}}, required=("query",))
TOOLS = [WEATHER, TICKET, STATUS, SEARCH]


def profile(**overrides):
    row = {
        "profile_id": "decision",
        "model_id": "m",
        "tier": "decision",
        "adapter": "multi_tool_plan",
        "dialect": "openai",
        "license_id": "Apache-2.0",
        "source_url": "https://example.invalid",
        "commercial_use_allowed": True,
        "research_only": False,
        "supports_confidence": True,
        "supports_parallel_tools": True,
        "max_tools": 16,
        "context_window": 8192,
        "min_confidence": 0.8,
        "metadata": {"endpoint_env": "PD_URL"},
    }
    row.update(overrides)
    return TinyActionModelProfile.from_mapping(row)


def field(value, probability=0.97, *, abstain=False):
    return {"value": value, "probability": probability, "abstain": abstain}


def response(schema, **values):
    fields = {name: field(False) for name in schema}
    fields.update(values)
    return {"object": "decision", "results": [{"fields": fields}]}


class Runtime:
    def __init__(self, answer=None, error=None, url="http://llama:8096"):
        self.answer, self.error, self.url, self.bodies = answer, error, url, []

    def base_url(self, profile):
        return self.url

    def decide(self, profile, body, *, timeout_ms):
        self.bodies.append((body, timeout_ms))
        if self.error:
            raise self.error
        return self.answer(body["schema"]) if callable(self.answer) else self.answer


@dataclass
class Spec:
    risk_class: str


class Registry:
    RISK = {"get_weather": "read", "create_ticket": "read", "hub.status": "read", "repo.search": "read"}

    def get_tool(self, name):
        return Spec(self.RISK[name]) if name in self.RISK else None


class SchemaAdapter:
    def get_openai_tools(self, allowed):
        return [item for item in TOOLS if item["function"]["name"] in allowed]


def router(runtime, **kwargs):
    return TinyToolRouterService(
        catalog=ProfileCatalog.from_profiles([profile()]),
        plan_adapters=[MultiToolPlanAdapter(runtime)],
        schema_adapter=SchemaAdapter(),
        registry=Registry(),
        **kwargs,
    )


CONFIG = {
    "mode": "active",
    "profile_order": ["decision"],
    "top_k": 8,
    "max_hops": 1,
    "max_total_ms": 2000,
    "allowed_risk_classes": ["read"],
    "commercial_use": True,
    "multi_tool_plan": True,
}
ALLOWED = ["get_weather", "create_ticket", "hub.status", "repo.search"]


# --- feature flag ------------------------------------------------------------------------------------


def test_plan_is_disabled_by_default():
    decision = router(Runtime()).plan(
        prompt="weather and a ticket", allowed_tools=ALLOWED, config={**CONFIG, "multi_tool_plan": False}
    )
    assert decision.status == "disabled" and decision.reason_code == "multi_tool_plan_disabled"
    assert decision.plan is None


def test_single_candidate_route_path_is_unchanged():
    from agent.services.tiny_router.parallel_decision import ParallelDecisionAdapter

    routed = TinyToolRouterService(
        catalog=ProfileCatalog.from_profiles([profile(adapter="parallel_decision")]),
        adapters=[ParallelDecisionAdapter(Runtime())],
        schema_adapter=SchemaAdapter(),
        registry=Registry(),
    ).route(prompt="status", allowed_tools=["hub.status"], config=CONFIG)
    assert routed.status == "escalate"  # no adapter answer configured: never a candidate


# --- multi-tool plans --------------------------------------------------------------------------------


def test_two_independent_tools_are_planned_in_one_request():
    runtime = Runtime(
        lambda schema: response(
            schema,
            get_weather__enabled=field(True, 0.97),
            get_weather__unit=field("celsius", 0.95),
            create_ticket__enabled=field(True, 0.93),
            create_ticket__priority=field("high", 0.9),
        )
    )
    decision = router(runtime).plan(
        prompt="what's the weather and open a ticket now", allowed_tools=ALLOWED, config=CONFIG
    )
    assert decision.status == "plan"
    assert {step.tool for step in decision.calls()} == {"get_weather", "create_ticket"}
    assert len(decision.calls()) == 2
    arguments = {step.tool: dict(step.arguments) for step in decision.calls()}
    assert arguments["get_weather"] == {"unit": "celsius"}
    assert arguments["create_ticket"] == {"priority": "high"}
    # one prefill: every authorized tool appears as an enabled field in one request
    body = runtime.bodies[0][0]
    assert body["contexts"] == ["what's the weather and open a ticket now"]
    assert {name for name in body["schema"] if name.endswith("__enabled")} == {
        "get_weather__enabled",
        "create_ticket__enabled",
        "hub_status__enabled",
        "repo_search__enabled",
    }
    assert decision.as_dict()["schema"] == "ananta.tiny_multi_tool_routing_decision.v1"


def test_a_negative_enabled_field_never_calls_the_tool():
    runtime = Runtime(
        lambda schema: response(
            schema,
            get_weather__enabled=field(False, 0.99),
            create_ticket__enabled=field(True, 0.95),
            create_ticket__priority=field("low", 0.95),
        )
    )
    decision = router(runtime).plan(prompt="open a ticket", allowed_tools=ALLOWED, config=CONFIG)
    assert [step.tool for step in decision.calls()] == ["create_ticket"]
    assert all(step.tool != "get_weather" for step in decision.calls())


def test_shadow_mode_returns_a_shadow_plan():
    runtime = Runtime(lambda schema: response(schema, hub_status__enabled=field(True, 0.99)))
    decision = router(runtime).plan(
        prompt="how is the hub?", allowed_tools=ALLOWED, config={**CONFIG, "mode": "shadow"}
    )
    assert decision.status == "shadow_plan" and decision.shadow is True
    assert decision.calls()[0].tool == "hub.status"


# --- abstain / escalation ------------------------------------------------------------------------------


def test_free_text_only_plan_escalates_instead_of_guessing():
    runtime = Runtime(lambda schema: response(schema, repo_search__enabled=field(True, 0.99)))
    decision = router(runtime).plan(prompt="find the docs", allowed_tools=["repo.search"], config=CONFIG)
    assert decision.status == "escalate" and decision.plan is None
    assert decision.attempts[-1].status == "abstain"


def test_no_tool_enabled_escalates():
    decision = router(Runtime(lambda schema: response(schema))).plan(
        prompt="tell me a joke", allowed_tools=ALLOWED, config=CONFIG
    )
    assert decision.status == "escalate"


def test_endpoint_failure_escalates():
    decision = router(Runtime(error=OSError("down"))).plan(prompt="status", allowed_tools=["hub.status"], config=CONFIG)
    assert decision.status == "escalate"


def test_write_tool_is_filtered_before_scoring_in_read_only_scope():
    runtime = Runtime(lambda schema: response(schema, hub_status__enabled=field(True, 0.99)))
    router(runtime).plan(
        prompt="restart the worker",
        allowed_tools=["get_weather", "hub.status"],
        config={**CONFIG, "allowed_risk_classes": ["read"]},
        mutation_mode="read_only",
    )
    enabled = [name for name in runtime.bodies[0][0]["schema"] if name.endswith("__enabled")]
    assert "get_weather__enabled" in enabled and "create_ticket__enabled" not in enabled


# --- adapter / validator -------------------------------------------------------------------------------


def test_the_request_never_reuses_a_context():
    runtime = Runtime(lambda schema: response(schema, hub_status__enabled=field(True)))
    MultiToolPlanAdapter(runtime).propose(AdapterRequest("status", (STATUS,), profile(), 900))
    body, timeout_ms = runtime.bodies[0]
    assert body["cache_context"] is False and timeout_ms == 900


def test_registry_validator_reports_missing_required_argument():
    validate = registry_argument_validator([SEARCH])
    assert "$:missing_required:query" in list(validate("repo.search", {"limit": 2}))
    assert list(validate("repo.search", {"query": "x"})) == []
    assert list(validate("unknown.tool", {})) == ["unknown_or_denied_tool"]


def test_unconfigured_endpoint_escalates():
    decision = router(Runtime(url="")).plan(prompt="status", allowed_tools=["hub.status"], config=CONFIG)
    assert decision.status == "escalate"


def test_configured_confidence_is_binding():
    runtime = Runtime(lambda s: response(s, hub_status__enabled=field(True, 0.90)))
    decision = router(runtime).plan(
        prompt="status", allowed_tools=["hub.status"], config={**CONFIG, "min_confidence": 0.99}
    )
    assert decision.status == "escalate" and not decision.calls()


def test_cancel_during_inference_cannot_publish_plan():
    cancelled = [False]

    def answer(schema):
        cancelled[0] = True
        return response(schema, hub_status__enabled=field(True))

    decision = router(Runtime(answer)).plan(
        prompt="status", allowed_tools=["hub.status"], config=CONFIG, cancel_check=lambda: cancelled[0]
    )
    assert decision.status == "escalate" and decision.reason_code == "invocation_cancelled"


def test_late_inference_cannot_publish_plan():
    now = [0.0]

    def answer(schema):
        now[0] = 3.0
        return response(schema, hub_status__enabled=field(True))

    decision = router(Runtime(answer), clock=lambda: now[0]).plan(
        prompt="status", allowed_tools=["hub.status"], config=CONFIG
    )
    assert decision.status == "escalate" and decision.reason_code == "routing_deadline_exceeded"


@pytest.mark.parametrize("status", ["partial", "error", "over_budget"])
def test_non_ready_plan_is_not_promoted(status):
    from agent.services.tiny_router.types import AdapterResult
    from ananta_contracts.multi_tool_plan import MultiToolPlan, ToolPlanStep

    class Adapter:
        adapter_id = "multi_tool_plan"

        def is_available(self, profile):
            return True, "ok"

        def propose(self, request):
            return AdapterResult(
                "candidate",
                MultiToolPlan(
                    status, status, (ToolPlanStep("hub.status", "call", "test", enabled=True, confidence=0.99),)
                ),
            )

    r = TinyToolRouterService(
        catalog=ProfileCatalog.from_profiles([profile()]),
        plan_adapters=[Adapter()],
        schema_adapter=SchemaAdapter(),
        registry=Registry(),
    )
    decision = r.plan(prompt="status", allowed_tools=["hub.status"], config=CONFIG)
    assert decision.status == "escalate" and not decision.calls()


def test_dependent_tools_escalate_before_scoring():
    runtime = Runtime()
    decision = router(runtime).plan(
        prompt="search then read its result",
        allowed_tools=ALLOWED,
        config={**CONFIG, "tool_dependencies": {"hub.status": ["repo.search"]}},
    )
    assert decision.reason_code == "dependent_tools_require_system2"
    assert not runtime.bodies
