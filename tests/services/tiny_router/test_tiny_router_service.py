from __future__ import annotations

from dataclasses import dataclass

from agent.services.tiny_router.observability import ListTelemetrySink
from agent.services.tiny_router.profiles import ProfileCatalog
from agent.services.tiny_router.service import TinyToolRouterService
from agent.services.tiny_router.types import AdapterResult, TinyActionModelProfile


def profile(profile_id="tiny", **overrides):
    row = {
        "profile_id": profile_id,
        "model_id": "test/model",
        "tier": "tiny",
        "adapter": "fake",
        "dialect": "openai",
        "license_id": "test",
        "source_url": "https://example.invalid",
        "commercial_use_allowed": True,
        "research_only": False,
        "supports_confidence": True,
        "supports_parallel_tools": False,
        "max_tools": 5,
        "context_window": 2048,
        "min_confidence": 0.8,
        "enabled_by_default": False,
    }
    row.update(overrides)
    return TinyActionModelProfile.from_mapping(row)


TOOL = {
    "type": "function",
    "function": {
        "name": "repo.search",
        "description": "Search repository",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}


class SchemaAdapter:
    def get_openai_tools(self, allowed_tools):
        return [TOOL] if "repo.search" in allowed_tools else []


@dataclass
class Spec:
    risk_class: str = "read"


class Registry:
    def get_tool(self, name):
        return Spec() if name == "repo.search" else None


class FakeAdapter:
    adapter_id = "fake"

    def __init__(self, payload=None, error=None):
        self.payload = payload or {
            "confidence": 0.95,
            "tool_calls": [{"name": "repo.search", "args": {"query": "hub"}}],
        }
        self.error = error
        self.calls = 0

    def is_available(self, profile):
        return True, "ready"

    def propose(self, request):
        self.calls += 1
        if self.error:
            raise self.error
        return AdapterResult("candidate", self.payload, latency_ms=3.0)


def service(adapter, profiles=None, sink=None):
    return TinyToolRouterService(
        catalog=ProfileCatalog.from_profiles(profiles or [profile()]),
        adapters=[adapter],
        schema_adapter=SchemaAdapter(),
        registry=Registry(),
        telemetry_sink=sink,
    )


def config(mode="active", order=None, **overrides):
    row = {
        "mode": mode,
        "profile_order": order or ["tiny"],
        "top_k": 5,
        "max_hops": 2,
        "max_total_ms": 1000,
        "allowed_risk_classes": ["read"],
    }
    row.update(overrides)
    return row


def test_disabled_mode_never_calls_adapter():
    adapter = FakeAdapter()
    decision = service(adapter).route(
        prompt="search",
        allowed_tools=["repo.search"],
        config=config("disabled"),
    )
    assert decision.status == "disabled"
    assert adapter.calls == 0


def test_active_mode_returns_validated_candidate():
    decision = service(FakeAdapter()).route(
        prompt="search hub",
        allowed_tools=["repo.search"],
        config=config(),
    )
    assert decision.status == "candidate"
    assert decision.candidate.tool_name == "repo.search"


def test_shadow_mode_never_returns_executable_status():
    decision = service(FakeAdapter()).route(
        prompt="search hub",
        allowed_tools=["repo.search"],
        config=config("shadow"),
    )
    assert decision.status == "shadow_candidate"
    assert decision.shadow


def test_invalid_candidate_escalates_to_main():
    adapter = FakeAdapter(
        payload={
            "confidence": 0.99,
            "tool_calls": [{"name": "shell.root", "args": {}}],
        }
    )
    decision = service(adapter).route(
        prompt="ignore policy",
        allowed_tools=["repo.search"],
        config=config(),
    )
    assert decision.status == "escalate"
    assert decision.escalation_tier == "main"


def test_runtime_failure_has_machine_reason_and_escalates():
    decision = service(FakeAdapter(error=TimeoutError())).route(
        prompt="search",
        allowed_tools=["repo.search"],
        config=config(),
    )
    assert decision.status == "escalate"
    assert decision.reason_code == "adapter_timeout"


def test_empty_allowed_scope_fails_closed():
    decision = service(FakeAdapter()).route(
        prompt="search",
        allowed_tools=[],
        config=config(),
    )
    assert decision.reason_code == "allowed_tool_scope_empty"


def test_kill_switch_prevents_runtime_use():
    adapter = FakeAdapter()
    decision = service(adapter).route(
        prompt="search",
        allowed_tools=["repo.search"],
        config=config(kill_switch=True),
    )
    assert decision.reason_code == "kill_switch_active"
    assert adapter.calls == 0


def test_cancellation_fences_candidate_before_and_after_runtime_call():
    adapter = FakeAdapter()
    cancelled = iter((False, False, True))

    decision = service(adapter).route(
        prompt="search",
        allowed_tools=["repo.search"],
        config=config(),
        cancel_check=lambda: next(cancelled),
    )

    assert decision.status == "escalate"
    assert decision.reason_code == "invocation_cancelled"
    assert decision.candidate is None
    assert adapter.calls == 1


def test_noncommercial_profile_is_not_selected_in_commercial_mode():
    restricted = profile(
        "restricted",
        commercial_use_allowed=False,
        research_only=True,
    )
    decision = service(FakeAdapter(), profiles=[restricted]).route(
        prompt="search",
        allowed_tools=["repo.search"],
        config=config(order=["restricted"], commercial_use=True),
    )
    assert decision.status == "escalate"
    assert decision.attempts[0].reason_code == "license_commercial_use_denied"


def test_telemetry_contains_no_prompt_or_arguments():
    sink = ListTelemetrySink()
    service(FakeAdapter(), sink=sink).route(
        prompt="private customer phrase",
        allowed_tools=["repo.search"],
        config=config(),
    )
    event = sink.events[0]
    assert "prompt" not in event
    assert "arguments" not in event
    assert event["prompt_chars"] == len("private customer phrase")
    assert event["attempts"] == [
        {
            "profile_id": "tiny",
            "tier": "tiny",
            "status": "valid",
            "reason_code": "candidate_validated",
            "latency_ms": 3.0,
            "selected_tool_count": 1,
        }
    ]
