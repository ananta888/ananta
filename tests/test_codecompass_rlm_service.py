from __future__ import annotations

from agent.services.codecompass_rlm_service import CodeCompassRlmService, rlm_is_eligible
from worker.rlm.recursive_query_planner import RecursiveQueryPlanner


def test_simple_query_falls_back() -> None:
    assert rlm_is_eligible("fix typo", enabled=True)[0] is False
    assert rlm_is_eligible("why does architecture fail across workers", enabled=True)[0] is True
    assert rlm_is_eligible("why does architecture fail across workers", enabled=False)[0] is False


def test_planner_respects_fanout() -> None:
    plan = RecursiveQueryPlanner(max_depth=2, max_fanout=2).create_plan(
        "why architecture",
        graph={"nodes": [{"id": "a", "title": "Hub"}, {"id": "b", "title": "Worker"}, {"id": "c", "title": "Store"}]},
    )
    assert len(plan.steps) <= 3
    assert plan.to_dict()["schema"] == "codecompass.rlm-recursive-plan.v1"


def test_rlm_respects_empty_scope() -> None:
    bound = CodeCompassRlmService().analyze(
        "why does the architecture of CodeCompass fail across workers",
        enabled=True,
        capability={"workspace_id": "ws", "revision": "", "allowed_paths": []},
    )
    assert bound["status"] == "error"
    assert bound["reason"] == "empty_scope"


def test_rlm_executes_and_traces(monkeypatch) -> None:
    class Fake:
        def retrieve(self, payload, capability=None):
            return {
                "status": "ok",
                "evidence": [{"id": "n1", "path": "a.py", "excerpt": "hello", "signals": ["exact"]}],
            }

    monkeypatch.setattr(
        "agent.services.codecompass_rlm_service.get_codecompass_agentic_retrieval_service",
        lambda: Fake(),
    )
    result = CodeCompassRlmService().analyze(
        "why does the architecture of CodeCompass fail across workers",
        enabled=True,
    )
    assert result["status"] == "executed"
    assert result["trace"]
    assert result["merged"]["evidence"]
