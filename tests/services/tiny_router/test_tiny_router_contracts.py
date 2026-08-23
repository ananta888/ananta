from __future__ import annotations

import json

import pytest

from agent.services.tiny_router.adapters import (
    CactusNeedleRuntime,
    NeedleCandidateAdapter,
    OpenAICompatibleActionAdapter,
)
from agent.services.tiny_router.benchmark import BenchmarkRunner, assert_no_sensitive_fields
from agent.services.tiny_router.profiles import ProfileCatalog
from agent.services.tiny_router.schema_dialects import ToolSchemaDialectAdapter
from agent.services.tiny_router.types import AdapterRequest, TinyActionModelProfile
from agent.services.tiny_router.validation import CandidateValidator


def profile(**overrides):
    values = {
        "profile_id": "test",
        "model_id": "test/model",
        "tier": "tiny",
        "adapter": "openai_compatible",
        "dialect": "openai",
        "license_id": "test",
        "source_url": "https://example.invalid/model",
        "commercial_use_allowed": True,
        "research_only": False,
        "supports_confidence": False,
        "supports_parallel_tools": False,
        "max_tools": 20,
        "context_window": 2048,
        "min_confidence": 0,
        "local_only": True,
        "enabled_by_default": False,
    }
    values.update(overrides)
    return TinyActionModelProfile.from_mapping(values)


def tools():
    return [{
        "type": "function",
        "function": {
            "name": "repo.search",
            "description": "Search the repository",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1},
                    "limit": {"type": "integer", "minimum": 1},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    }]


def test_profile_rejects_invalid_confidence():
    with pytest.raises(ValueError, match="confidence"):
        profile(min_confidence=1.1)


def test_profile_catalog_enters_safe_mode_for_unknown_schema(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text('{"schema":"future.v2","profiles":[]}', encoding="utf-8")
    catalog = ProfileCatalog.load(path)
    assert catalog.safe_mode
    assert catalog.profiles == ()


def test_profile_catalog_rejects_noncommercial_xlam():
    catalog = ProfileCatalog.from_profiles([
        profile(
            profile_id="xlam",
            license_id="CC-BY-NC-4.0",
            commercial_use_allowed=False,
            research_only=True,
        )
    ])
    selected, rejected = catalog.ordered(
        ["xlam"], commercial_use=True, allow_research_only=False,
    )
    assert selected == ()
    assert rejected == (("xlam", "license_commercial_use_denied"),)


def test_openai_projection_is_lossless():
    projection = ToolSchemaDialectAdapter().project(tools(), dialect="openai")
    assert projection.tools[0]["function"]["name"] == "repo.search"
    assert projection.losses == ()


def test_needle_projection_removes_only_transport_envelope():
    projection = ToolSchemaDialectAdapter().project(tools(), dialect="needle")
    assert projection.tools[0]["name"] == "repo.search"
    assert projection.tools[0]["parameters"]["required"] == ["query"]


def test_xlam_projection_reports_constraint_loss():
    projection = ToolSchemaDialectAdapter().project(tools(), dialect="xlam")
    assert projection.tools[0]["name"] == "repo.search"
    assert "required_not_represented:repo.search" in projection.losses


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ({"tool_calls": [{"name": "unknown", "args": {}}]}, "unknown_or_denied_tool"),
        (
            {"tool_calls": [{"name": "repo.search", "args": {"query": "x", "limit": "5"}}]},
            "arguments_failed_schema",
        ),
        (
            {"tool_calls": [{"name": "repo.search", "args": {"query": "x", "extra": True}}]},
            "arguments_failed_schema",
        ),
        (
            {"tool_calls": [
                {"name": "repo.search", "args": {"query": "x"}},
                {"name": "repo.search", "args": {"query": "y"}},
            ]},
            "multiple_calls_not_supported",
        ),
    ],
)
def test_validator_fails_closed(payload, reason):
    result = CandidateValidator().validate(
        payload, tools=tools(), profile=profile(), adapter_id="test",
    )
    assert result.status == "invalid"
    assert result.reason_code == reason


def test_validator_accepts_fenced_json_without_semantic_repair():
    marker = chr(96) * 3
    payload = (
        marker + "json\n"
        + '{"tool_calls":[{"name":"repo.search","args":{"query":"hub","limit":2}}]}'
        + "\n" + marker
    )
    result = CandidateValidator().validate(
        payload, tools=tools(), profile=profile(), adapter_id="test",
    )
    assert result.candidate.tool_name == "repo.search"
    assert result.candidate.arguments == {"query": "hub", "limit": 2}


def test_validator_rejects_duplicate_json_keys():
    payload = '{"tool_calls":[{"name":"repo.search","args":{"query":"a","query":"b"}}]}'
    result = CandidateValidator().validate(
        payload, tools=tools(), profile=profile(), adapter_id="test",
    )
    assert result.reason_code == "invalid_json"


def test_confidence_gate_abstains():
    result = CandidateValidator().validate(
        {
            "confidence": 0.4,
            "function_calls": [{"name": "repo.search", "arguments": {"query": "hub"}}],
        },
        tools=tools(),
        profile=profile(
            adapter="needle", dialect="needle",
            supports_confidence=True, min_confidence=0.9,
        ),
        adapter_id="needle",
    )
    assert result.status == "abstain"
    assert result.reason_code == "below_confidence_threshold"


class NeedleRuntime:
    def __init__(self):
        self.complete_calls = 0

    def is_available(self):
        return True, "ready"

    def complete(self, **kwargs):
        self.complete_calls += 1
        assert "tools" in kwargs
        return {
            "type": "call",
            "function_calls": [{"name": "repo.search", "arguments": {"query": "hub"}}],
            "confidence": 0.95,
        }


def test_needle_adapter_requests_candidates_without_execution():
    runtime = NeedleRuntime()
    result = NeedleCandidateAdapter(runtime).propose(AdapterRequest(
        prompt="search hub",
        tools=tuple(tools()),
        profile=profile(
            adapter="needle", dialect="needle", supports_confidence=True,
        ),
        timeout_ms=100,
    ))
    assert runtime.complete_calls == 1
    assert result.payload["function_calls"][0]["name"] == "repo.search"


def test_cactus_runtime_resolves_deployment_weights_from_environment(monkeypatch):
    captured = {}

    class Needle:
        def __init__(self, *, tools, weights):
            captured.update(tools=tools, weights=weights)

        def complete(self, prompt):
            return {"type": "abstain", "prompt": prompt}

    monkeypatch.setenv("TEST_NEEDLE_WEIGHTS", "/models/needle2.cact")
    monkeypatch.setitem(__import__("sys").modules, "needle", type("Module", (), {"Needle": Needle}))
    result = CactusNeedleRuntime().complete(
        prompt="status", tools=[],
        profile=profile(
            adapter="needle", dialect="needle",
            metadata={"weights_env": "TEST_NEEDLE_WEIGHTS"},
        ),
        timeout_ms=100,
    )

    assert captured["weights"] == "/models/needle2.cact"
    assert result["type"] == "abstain"


class Transport:
    def __init__(self):
        self.tool_calls = 0
        self.text_calls = 0

    def invoke_with_tools(self, prompt, tools, *, model, timeout_seconds):
        self.tool_calls += 1
        return {"tool_calls": []}

    def invoke_text(self, prompt, *, model, timeout_seconds):
        self.text_calls += 1
        return json.dumps({"tool_calls": []})


def test_generic_adapter_reuses_injected_tool_transport():
    transport = Transport()
    OpenAICompatibleActionAdapter(transport).propose(
        AdapterRequest("x", tuple(tools()), profile(), 100)
    )
    assert transport.tool_calls == 1
    assert transport.text_calls == 0


def test_xlam_adapter_reuses_injected_text_transport():
    transport = Transport()
    OpenAICompatibleActionAdapter(transport).propose(
        AdapterRequest("x", tuple(tools()), profile(dialect="xlam"), 100)
    )
    assert transport.text_calls == 1
    assert transport.tool_calls == 0


def test_benchmark_covers_required_catalog_sizes():
    report = BenchmarkRunner().run(
        [{
            "model_output": {
                "tool_calls": [{"name": "repo.search", "args": {"query": "hub"}}]
            },
            "expected": {
                "tool_name": "repo.search", "arguments": {"query": "hub"},
            },
        }],
        tools=tools(), profile=profile(),
    )
    assert [row["requested"] for row in report.catalog_sizes] == [5, 20, 50, 100]
    assert all(row["selected"] == 5 for row in report.catalog_sizes)


def test_dataset_guard_rejects_secret_fields():
    with pytest.raises(ValueError, match="sensitive_dataset_field"):
        assert_no_sensitive_fields({"api_token": "not-allowed"})
