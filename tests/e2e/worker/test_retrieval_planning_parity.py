from __future__ import annotations

from worker.planning.planner import build_dependency_plan
from worker.retrieval.retrieval_service import HybridRetrievalService


def test_retrieval_planning_parity_between_hub_and_standalone_like_inputs() -> None:
    service = HybridRetrievalService()
    retrieval = service.retrieve(
        query="fix bug in auth",
        pipeline_contract={"channels": ["dense", "lexical"], "fallback_order": ["dense", "lexical"]},
        channel_results={
            "dense": [{"path": "src/auth.py", "content_hash": "h1", "score": 0.9, "text": "auth bug fix"}],
            "lexical": [{"path": "tests/test_auth.py", "content_hash": "h2", "score": 0.8, "text": "auth tests failing"}],
        },
        top_k=2,
    )
    plan = build_dependency_plan(
        task_id="AW-T40",
        profile="balanced",
        steps=[
            {"step_id": "inspect", "title": "Inspect failing code"},
            {"step_id": "patch", "title": "Apply fix", "depends_on": ["inspect"]},
            {"step_id": "verify", "title": "Run tests", "depends_on": ["patch"]},
        ],
    )
    assert retrieval["selected"][0]["path"] == "src/auth.py"
    assert [step["step_id"] for step in plan["steps"]] == ["inspect", "patch", "verify"]

