from __future__ import annotations

import json

import pytest

from agent.cli_backends.tool_loop import run_ananta_worker_tool_loop
from agent.services.tiny_router.types import RoutingDecision, ToolCallCandidate


class Router:
    def __init__(self, status):
        self.status = status
        self.calls = 0

    def route(self, **kwargs):
        self.calls += 1
        candidate = ToolCallCandidate("git.status", {}, 0.99, "test", "fake")
        return RoutingDecision(
            self.status,
            "test",
            candidate=candidate,
            shadow=self.status == "shadow_candidate",
        )


class UnifiedExecutor:
    def __init__(self):
        self.calls = 0

    def execute(self, **kwargs):
        self.calls += 1
        return {
            "schema": "ananta_tool_result.v1",
            "tool_call_id": kwargs["tool_call_id"],
            "tool_name": kwargs["tool_name"],
            "status": "ok",
            "risk_class": "read",
            "evidence": [],
            "warnings": [],
            "policy_decision": {"decision": "allow"},
        }


def final_runner(counter):
    def run(**kwargs):
        counter.append(kwargs)
        return 0, json.dumps({"kind": "final_answer", "answer": "done"}), ""

    return run


def test_active_candidate_executes_exactly_once_through_unified_gateway(
    monkeypatch,
    tmp_path,
):
    router = Router("candidate")
    executor = UnifiedExecutor()
    main_calls = []
    monkeypatch.setattr(
        "agent.services.tiny_router.service.get_tiny_tool_router_service",
        lambda: router,
    )
    monkeypatch.setattr(
        "agent.services.unified_tool_execution_service.get_unified_tool_execution_service",
        lambda: executor,
    )
    rc, out, _ = run_ananta_worker_tool_loop(
        "show status",
        str(tmp_path),
        options=[],
        timeout=5,
        model="test",
        llm_runner=final_runner(main_calls),
        config={
            "max_iterations": 3,
            "max_tool_calls": 3,
            "max_tool_result_chars": 1000,
            "max_invalid_outputs": 1,
            "allowed_tools": ["git.status"],
            "tiny_router": {"mode": "active", "profile_order": ["test"]},
        },
    )
    assert rc == 0
    assert out == "done"
    assert router.calls == 1
    assert executor.calls == 1
    assert len(main_calls) == 1


def test_shadow_candidate_always_uses_main_model(monkeypatch, tmp_path):
    router = Router("shadow_candidate")
    main_calls = []
    monkeypatch.setattr(
        "agent.services.tiny_router.service.get_tiny_tool_router_service",
        lambda: router,
    )
    rc, out, _ = run_ananta_worker_tool_loop(
        "show status",
        str(tmp_path),
        options=[],
        timeout=5,
        model="test",
        llm_runner=final_runner(main_calls),
        config={
            "max_iterations": 2,
            "max_tool_calls": 2,
            "max_tool_result_chars": 1000,
            "max_invalid_outputs": 1,
            "allowed_tools": ["git.status"],
            "tiny_router": {"mode": "shadow", "profile_order": ["test"]},
        },
    )
    assert rc == 0
    assert out == "done"
    assert router.calls == 1
    assert len(main_calls) == 1


@pytest.mark.parametrize("failure", ["timeout", "invalid_candidate"])
def test_needle_failure_escalates_to_main_without_tool_execution(
    monkeypatch,
    tmp_path,
    failure,
):
    class FailedNeedleRouter:
        def route(self, **_kwargs):
            if failure == "timeout":
                raise TimeoutError("bounded needle timeout")
            return RoutingDecision("escalate", "confidence_invalid", escalation_tier="main")

    executor = UnifiedExecutor()
    main_calls = []
    monkeypatch.setattr(
        "agent.services.tiny_router.service.get_tiny_tool_router_service",
        lambda: FailedNeedleRouter(),
    )
    monkeypatch.setattr(
        "agent.services.unified_tool_execution_service.get_unified_tool_execution_service",
        lambda: executor,
    )

    rc, out, _ = run_ananta_worker_tool_loop(
        "show status",
        str(tmp_path),
        options=[],
        timeout=5,
        model="local_lfm25_agentic_fast",
        llm_runner=final_runner(main_calls),
        config={
            "max_iterations": 2,
            "max_tool_calls": 2,
            "max_tool_result_chars": 1000,
            "max_invalid_outputs": 1,
            "allowed_tools": ["git.status"],
            "tiny_router": {"mode": "active", "profile_order": ["needle-2-45m"]},
        },
    )

    assert rc == 0
    assert out == "done"
    assert executor.calls == 0
    assert len(main_calls) == 1
