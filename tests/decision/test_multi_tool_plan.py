"""PDT: backend-neutral multi-tool plan contract (schema, request body, reader).

Headless: no network, no model. Responses are hand-built mappings in the
llama.cpp ``/v1/decision`` shape.
"""

from __future__ import annotations

import pytest

from ananta_contracts.multi_tool_plan import (
    ABSTAIN,
    ERROR,
    OVER_BUDGET,
    PARTIAL,
    PLAN,
    PLAN_SCHEMA,
    RESPOND,
    SKIPPED,
    build_multi_tool_plan_schema,
    plan_fingerprint,
    plan_from_mapping,
    plan_request_body,
    read_multi_tool_plan,
)

pytestmark = pytest.mark.timeout(30)

SEARCH = {
    "type": "function",
    "function": {
        "name": "search_files",
        "description": "Find files by glob.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 5}},
            "required": ["query", "limit"],
        },
    },
}
WEATHER = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Weather for a place.",
        "parameters": {
            "type": "object",
            "properties": {
                "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
                "detailed": {"type": "boolean"},
            },
            "required": ["unit"],
        },
    },
}
TICKET = {
    "type": "function",
    "function": {
        "name": "create_ticket",
        "description": "Open a ticket.",
        "parameters": {
            "type": "object",
            "properties": {"priority": {"type": "string", "enum": ["low", "high"]}},
            "required": ["priority"],
        },
    },
}
EXTERNAL = {
    "type": "function",
    "function": {
        "name": "github.create_issue",
        "description": "Open an issue.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}


def _field(value, probability=1.0, *, abstain=False):
    row = {"value": value, "probability": probability}
    if abstain:
        row["abstain"] = True
    return row


def _response(schema, **fields):
    filled = {}
    for name in schema.schema:
        filled[name] = fields.get(name, _field(False))
    return {"object": "decision", "results": [{"fields": filled}]}


def test_schema_builds_enabled_argument_and_text_fields():
    schema = build_multi_tool_plan_schema([SEARCH, WEATHER, TICKET])
    assert schema.tools == ("search_files", "get_weather", "create_ticket")
    assert schema.enabled_field("get_weather") in schema.schema
    assert schema.schema[schema.enabled_field("get_weather")]["type"] == "boolean"
    # limit is closed (integer 1..5), unit and priority are enum arguments
    closed = {(item.tool, item.argument): item.values for item in schema.arguments}
    assert closed[("search_files", "limit")] == (1, 2, 3, 4, 5)
    assert closed[("get_weather", "unit")] == ("celsius", "fahrenheit")
    assert closed[("create_ticket", "priority")] == ("low", "high")
    # the free-text query is recorded, not scored
    assert [(t.tool, t.argument) for t in schema.text_fields] == [("search_files", "query")]
    assert schema.free_text_tools == frozenset()
    assert schema.dependencies[schema.enabled_field("search_files")] == "independent"
    assert schema.dependencies[schema.arguments[0].field_name].startswith("grouped:")


def test_request_body_shares_one_context():
    schema = build_multi_tool_plan_schema([SEARCH, WEATHER])
    body = plan_request_body(schema, "Wie ist das Wetter in Stuttgart?", model_id="bonsai")
    assert body["contexts"] == ["Wie ist das Wetter in Stuttgart?"]
    assert body["schema"] == schema.schema
    assert body["mode"] == "tree"
    assert body["cache_context"] is False
    assert body["model"] == "bonsai"


def test_unrepresentable_required_argument_marks_tool_for_system2():
    tool = {
        "type": "function",
        "function": {
            "name": "deploy",
            "parameters": {"type": "object", "properties": {"config": {"type": "object"}}, "required": ["config"]},
        },
    }
    schema = build_multi_tool_plan_schema([tool])
    assert "deploy" in schema.free_text_tools


def test_two_independent_tools_plan_without_free_text():
    schema = build_multi_tool_plan_schema([WEATHER, TICKET])
    response = _response(
        schema,
        **{
            schema.enabled_field("get_weather"): _field(True, 0.97),
            schema.enabled_field("create_ticket"): _field(True, 0.95),
            next(a.field_name for a in schema.arguments_of("get_weather")): _field("celsius", 0.99),
            next(a.field_name for a in schema.arguments_of("create_ticket")): _field("high", 0.91),
        },
    )
    plan = read_multi_tool_plan(response, schema, min_confidence=0.8)
    assert plan.status == PLAN
    assert [step.tool for step in plan.calls()] == ["get_weather", "create_ticket"]
    assert plan.calls()[0].arguments == {"unit": "celsius"}
    assert plan.calls()[1].arguments == {"priority": "high"}
    assert plan.calls()[0].confidence == pytest.approx(0.97)
    assert plan.router_payload()["tool_calls"][0]["name"] == "get_weather"
    assert plan_fingerprint(plan) == plan_fingerprint(plan_from_mapping(plan.to_mapping()))


def test_disabled_tool_never_calls():
    schema = build_multi_tool_plan_schema([WEATHER, TICKET])
    response = _response(
        schema,
        **{
            schema.enabled_field("get_weather"): _field(False, 0.99),
            schema.enabled_field("create_ticket"): _field(True, 0.9),
            next(a.field_name for a in schema.arguments_of("create_ticket")): _field("low", 0.9),
        },
    )
    plan = read_multi_tool_plan(response, schema, min_confidence=0.8)
    skipped = next(step for step in plan.steps if step.tool == "get_weather")
    assert skipped.status == SKIPPED and skipped.reason == "not_selected"
    assert [step.tool for step in plan.calls()] == ["create_ticket"]


def test_all_disabled_is_respond():
    schema = build_multi_tool_plan_schema([WEATHER, TICKET])
    plan = read_multi_tool_plan(_response(schema), schema, min_confidence=0.8)
    assert plan.status == RESPOND
    assert plan.calls() == ()


def test_uncertain_enabled_field_abstains():
    schema = build_multi_tool_plan_schema([WEATHER])
    response = _response(schema, **{schema.enabled_field("get_weather"): _field(True, 0.4)})
    plan = read_multi_tool_plan(response, schema, min_confidence=0.8)
    assert plan.status == ABSTAIN
    assert plan.steps[0].status == ABSTAIN and plan.steps[0].reason == "enabled_uncertain"


def test_uncertain_argument_makes_plan_partial():
    schema = build_multi_tool_plan_schema([WEATHER, TICKET])
    response = _response(
        schema,
        **{
            schema.enabled_field("get_weather"): _field(True, 0.99),
            next(a.field_name for a in schema.arguments_of("get_weather")): _field("celsius", 0.5),
            schema.enabled_field("create_ticket"): _field(True, 0.9),
            next(a.field_name for a in schema.arguments_of("create_ticket")): _field("low", 0.9),
        },
    )
    plan = read_multi_tool_plan(response, schema, min_confidence=0.8)
    assert plan.status == PARTIAL
    weather = next(step for step in plan.steps if step.tool == "get_weather")
    assert weather.status == ABSTAIN and weather.reason == "argument_uncertain"
    assert [step.tool for step in plan.calls()] == ["create_ticket"]


def test_unknown_scored_value_is_rejected():
    schema = build_multi_tool_plan_schema([WEATHER])
    response = _response(
        schema,
        **{
            schema.enabled_field("get_weather"): _field(True, 0.99),
            next(a.field_name for a in schema.arguments_of("get_weather")): _field("kelvin", 0.99),
        },
    )
    from ananta_contracts.multi_tool_plan import PlanResponseError

    with pytest.raises(PlanResponseError, match="plan_value_not_allowed"):
        read_multi_tool_plan(response, schema, min_confidence=0.8)


def test_free_text_required_without_open_field_abstains():
    schema = build_multi_tool_plan_schema([SEARCH])
    enabled = schema.enabled_field("search_files")
    limit = next(a.field_name for a in schema.arguments_of("search_files"))
    response = _response(schema, **{enabled: _field(True, 0.99), limit: _field(3, 0.99)})
    plan = read_multi_tool_plan(response, schema, min_confidence=0.8)
    assert plan.status == ABSTAIN
    step = plan.steps[0]
    assert step.status == ABSTAIN and step.reason == "free_text_required"
    assert step.missing_arguments == ("query",)


def test_open_field_generates_the_single_free_text_argument():
    schema = build_multi_tool_plan_schema([SEARCH], open_field=True)
    assert schema.open_field_tool == "search_files"
    enabled = schema.enabled_field("search_files")
    limit = next(a.field_name for a in schema.arguments_of("search_files"))
    response = _response(
        schema,
        **{
            enabled: _field(True, 0.99),
            limit: _field(2, 0.99),
            "text_argument": {"generated": True, "value": "docs/**/*.md", "truncated": False},
        },
    )
    plan = read_multi_tool_plan(response, schema, min_confidence=0.8)
    assert plan.status == PLAN
    step = plan.calls()[0]
    assert step.arguments == {"query": "docs/**/*.md", "limit": 2}
    assert step.generated_arguments == ("query",)
    assert plan.router_payload()["generated_arguments"] == ["query"]


def test_open_field_truncated_is_not_a_call():
    schema = build_multi_tool_plan_schema([SEARCH], open_field=True)
    enabled = schema.enabled_field("search_files")
    limit = next(a.field_name for a in schema.arguments_of("search_files"))
    response = _response(
        schema,
        **{
            enabled: _field(True, 0.99),
            limit: _field(2, 0.99),
            "text_argument": {"generated": True, "value": "partial", "truncated": True},
        },
    )
    plan = read_multi_tool_plan(response, schema, min_confidence=0.8)
    assert plan.steps[0].status == ABSTAIN and plan.steps[0].reason == "free_text_required"


def test_over_budget_flags_more_calls_than_allowed():
    schema = build_multi_tool_plan_schema([WEATHER, TICKET])
    response = _response(
        schema,
        **{
            schema.enabled_field("get_weather"): _field(True, 0.99),
            next(a.field_name for a in schema.arguments_of("get_weather")): _field("celsius", 0.99),
            schema.enabled_field("create_ticket"): _field(True, 0.99),
            next(a.field_name for a in schema.arguments_of("create_ticket")): _field("low", 0.99),
        },
    )
    plan = read_multi_tool_plan(response, schema, min_confidence=0.8, max_calls=1)
    assert plan.status == OVER_BUDGET
    assert len(plan.calls()) == 2  # the plan is not silently trimmed


def test_argument_validator_can_fail_a_step():
    schema = build_multi_tool_plan_schema([WEATHER])
    response = _response(
        schema,
        **{
            schema.enabled_field("get_weather"): _field(True, 0.99),
            next(a.field_name for a in schema.arguments_of("get_weather")): _field("celsius", 0.99),
        },
    )
    plan = read_multi_tool_plan(
        response,
        schema,
        min_confidence=0.8,
        argument_validator=lambda tool, args: ["unit_required"] if tool == "get_weather" else [],
    )
    assert plan.status == ERROR
    assert plan.steps[0].status == ERROR and plan.steps[0].reason == "arguments_failed_schema"
    assert plan.steps[0].missing_arguments == ("unit_required",)


def test_field_set_mismatch_is_rejected():
    from ananta_contracts.multi_tool_plan import PlanResponseError

    schema = build_multi_tool_plan_schema([WEATHER])
    response = {"object": "decision", "results": [{"fields": {"unexpected": _field(False)}}]}
    with pytest.raises(PlanResponseError, match="plan_fields_mismatch"):
        read_multi_tool_plan(response, schema, min_confidence=0.8)


def test_mapping_round_trip_preserves_fields():
    schema = build_multi_tool_plan_schema([WEATHER, TICKET])
    response = _response(
        schema,
        **{
            schema.enabled_field("get_weather"): _field(True, 0.97),
            next(a.field_name for a in schema.arguments_of("get_weather")): _field("fahrenheit", 0.93),
            schema.enabled_field("create_ticket"): _field(False, 0.9),
        },
    )
    plan = read_multi_tool_plan(response, schema, min_confidence=0.8)
    restored = plan_from_mapping(plan.to_mapping())
    assert restored.status == plan.status
    assert [step.tool for step in restored.calls()] == ["get_weather"]
    assert restored.calls()[0].arguments == {"unit": "fahrenheit"}
    assert restored.schema_version == PLAN_SCHEMA


@pytest.mark.parametrize(
    "tools",
    [
        [{"name": "t", "parameters": {"properties": {"enabled": {"type": "boolean"}}, "required": ["enabled"]}}],
        [{"name": "a.b"}, {"name": "a_b"}],
        [
            {
                "name": "t",
                "parameters": {
                    "properties": {"a.b": {"type": "boolean"}, "a_b": {"type": "boolean"}},
                    "required": ["a.b", "a_b"],
                },
            }
        ],
    ],
)
def test_field_collisions_fail_closed(tools):
    from ananta_contracts.multi_tool_plan import PlanSchemaError

    with pytest.raises(PlanSchemaError, match="collision"):
        build_multi_tool_plan_schema(tools)


def test_activation_field_counts_towards_budget():
    properties = {f"a{i}": {"type": "boolean"} for i in range(32)}
    schema = build_multi_tool_plan_schema(
        [{"name": "t", "parameters": {"properties": properties, "required": list(properties)}}]
    )
    assert len(schema.schema) <= 32
    plan = read_multi_tool_plan(_response(schema, t__enabled=_field(True)), schema, min_confidence=0.8)
    assert plan.status == ABSTAIN


def test_multiple_free_arguments_escalate_without_guessing():
    schema = build_multi_tool_plan_schema(
        [
            {
                "name": "t",
                "parameters": {
                    "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
                    "required": ["a", "b"],
                },
            }
        ],
        open_field=True,
    )
    assert schema.open_field_tool is None
    plan = read_multi_tool_plan(_response(schema, t__enabled=_field(True)), schema, min_confidence=0.8)
    assert not plan.calls()


def test_integer_candidates_do_not_accept_booleans():
    from ananta_contracts.multi_tool_plan import PlanResponseError

    schema = build_multi_tool_plan_schema(
        [
            {
                "name": "t",
                "parameters": {"properties": {"count": {"type": "integer", "enum": [1, 0]}}, "required": ["count"]},
            }
        ]
    )
    assert schema.schema["t__count"]["type"] == "enum"
    with pytest.raises(PlanResponseError, match="value_not_allowed"):
        read_multi_tool_plan(
            _response(schema, t__enabled=_field(True), t__count=_field(True)), schema, min_confidence=0.8
        )


def test_conditional_argument_schema_requires_system2():
    tool = {
        "name": "t",
        "parameters": {"type": "object", "properties": {}, "if": {"type": "object"}, "then": {"required": ["query"]}},
    }
    schema = build_multi_tool_plan_schema([tool])
    result = read_multi_tool_plan(_response(schema, t__enabled=_field(True)), schema, min_confidence=0.8)
    assert result.status == ABSTAIN and not result.calls()


def test_skipped_open_field_does_not_invalidate_other_calls():
    schema = build_multi_tool_plan_schema([SEARCH, WEATHER], open_field=True)
    result = read_multi_tool_plan(
        _response(
            schema,
            get_weather__enabled=_field(True),
            get_weather__unit=_field("celsius"),
            text_argument={"generated": False, "skipped": True},
        ),
        schema,
        min_confidence=0.8,
    )
    assert result.status == PLAN
    assert [step.tool for step in result.calls()] == ["get_weather"]
