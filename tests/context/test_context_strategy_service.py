"""LCTX-004: the Hub decides how a task whose context exceeds the window is handled."""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from agent.context_window import ContextFit
from agent.services import context_strategy_service as cs

pytestmark = pytest.mark.timeout(30)
WINDOW = 32768


def _fit(ratio):
    budget = WINDOW - 1024
    return ContextFit(WINDOW, 1024, int(budget * ratio))


def _decide(ratio, **kwargs):
    return cs.ContextStrategyService(config={"ask_decision_provider": False}).decide(
        cs.ContextStrategyRequest(fit=_fit(ratio), **kwargs))


@pytest.mark.parametrize("ratio, kwargs, strategy", [
    (0.8, {}, "fit"),
    (1.3, {"input_kind": "ordered"}, "compact"),  # slightly over: condense, whatever the shape
    (6.0, {"input_kind": "conversation"}, "compact"),
    (6.0, {"input_kind": "corpus"}, "retrieve"),
    (6.0, {"input_kind": "unknown", "question_like": True}, "retrieve"),
    (6.0, {"input_kind": "parts", "parts": 40}, "map_reduce"),
    (6.0, {"input_kind": "ordered"}, "sequential"),
    (6.0, {"input_kind": "ordered", "question_like": True}, "sequential"),  # order wins over a question
    (60.0, {"input_kind": "parts"}, "escalate"),
])
def test_rules_choose_the_strategy(ratio, kwargs, strategy):
    decision = _decide(ratio, **kwargs)
    assert decision.strategy == strategy and decision.decided_by == "rules"


def test_chunked_strategies_get_budgeted_parameters():
    parameters = _decide(6.0, input_kind="parts", parts=40).parameters
    assert parameters["chunk_budget_tokens"] == int((WINDOW - 1024) * 0.6)
    assert parameters["chunks"] == math.ceil(int((WINDOW - 1024) * 6.0) / parameters["chunk_budget_tokens"])
    assert parameters["parallelism"] == 4
    assert "parallelism" not in _decide(6.0, input_kind="ordered").parameters


def test_the_grey_zone_defaults_to_sequential_or_asks_the_decision_provider():
    grey = _decide(6.0, input_kind="unknown")
    assert (grey.strategy, grey.decided_by) == ("sequential", "default")

    class Decisions:
        def area_mode(self, area):
            assert area == "context_strategy"
            return "active"

        def decide(self, area, request):
            options = request.question("strategy").labels
            assert "map_reduce" in options and "fit" not in options
            answer = SimpleNamespace(choice="map_reduce", confidence=0.93)
            result = SimpleNamespace(answer=lambda key: answer)
            return SimpleNamespace(usable=True, outcome=SimpleNamespace(result=result))

    service = cs.ContextStrategyService(decision_service=Decisions())
    decision = service.decide(cs.ContextStrategyRequest(fit=_fit(6.0), description="Fasse alle 40 Protokolle zusammen"))
    assert (decision.strategy, decision.decided_by) == ("map_reduce", "decision_provider")
    assert decision.parameters["parallelism"] == 4


def test_config_is_clamped_and_modes_are_closed():
    cfg = cs.normalize_config({"mode": "bogus", "chunk_fill": 5, "compact_max_ratio": "x", "max_parallel": 99})
    assert cfg["mode"] == "shadow" and cfg["chunk_fill"] == 0.9
    assert cfg["compact_max_ratio"] == cs.DEFAULTS["compact_max_ratio"] and cfg["max_parallel"] == 32
    assert cs.normalize_config({"max_parallel": 0})["max_parallel"] == cs.DEFAULTS["max_parallel"]  # 0 = unset
    assert cs.normalize_config({"request_overhead_tokens": 99999})["request_overhead_tokens"] == 24000
    assert cs.normalize_config({"request_overhead_tokens": "x"})["request_overhead_tokens"] == 12000
    assert cs.normalize_config({})["externalize"] is False
    from agent.config_defaults import build_default_agent_config

    assert build_default_agent_config()["context_strategy"]["mode"] == "shadow"


def test_post_config_rejects_unknown_modes(client, admin_auth_header):
    bad = client.post("/config", json={"context_strategy": {"mode": "sometimes"}}, headers=admin_auth_header)
    assert bad.status_code == 400 and b"invalid_context_strategy_mode" in bad.data
    ok = client.post("/config", json={"context_strategy": {"mode": "active", "chunk_fill": 0.5}},
                     headers=admin_auth_header)
    assert ok.status_code == 200


def test_propose_records_a_decision_only_when_the_context_does_not_fit(monkeypatch):
    from agent.services import _task_scoped_propose_orch as orch

    events = []
    monkeypatch.setattr(orch, "record_product_event", lambda name, **kwargs: events.append((name, kwargs)))
    task = {"title": "Protokolle", "description": "Fasse die Protokolle zusammen", "context_input_kind": "parts",
            "goal_id": "g1"}
    orch._record_context_strategy(task, "t1", "analysis", "kurz", {"prompt_section": "klein"}, {})
    assert events == []
    big = {"prompt_section": "x" * (WINDOW * 4 * 3)}
    orch._record_context_strategy(task, "t1", "analysis", "prompt", big, {})
    name, kwargs = events[0]
    assert name == "context_strategy_decided" and kwargs["details"]["strategy"] == "retrieve"  # question over material
    assert kwargs["details"]["mode"] == "shadow" and kwargs["details"]["fit"]["ratio"] > 2
    orch._record_context_strategy(task, "t1", "analysis", "prompt", big, {"context_strategy": {"mode": "off"}})
    assert len(events) == 1
