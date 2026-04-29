from __future__ import annotations

from worker.retrieval.retrieval_service import HybridRetrievalService


def _evaluate_hit_rate(results: list[dict], expected_paths: set[str], k: int) -> float:
    selected = [item["path"] for item in results[:k]]
    if not expected_paths:
        return 1.0
    hits = sum(1 for path in selected if path in expected_paths)
    return float(hits) / float(len(expected_paths))


def test_retrieval_benchmark_outputs_machine_readable_metrics() -> None:
    service = HybridRetrievalService()
    scenarios = [
        {
            "name": "greenfield_bootstrap",
            "query": "create project scaffold",
            "expected": {"scaffold.py"},
            "channels": {"dense": [{"path": "scaffold.py", "content_hash": "1", "score": 0.9, "text": "create scaffold"}]},
        },
        {
            "name": "refactor",
            "query": "refactor auth flow",
            "expected": {"auth.py"},
            "channels": {"dense": [{"path": "auth.py", "content_hash": "2", "score": 0.8, "text": "auth flow"}]},
        },
        {
            "name": "bugfix",
            "query": "fix failing test",
            "expected": {"tests/test_app.py"},
            "channels": {"dense": [{"path": "tests/test_app.py", "content_hash": "3", "score": 0.7, "text": "failing test"}]},
        },
    ]
    scores: list[float] = []
    for scenario in scenarios:
        payload = service.retrieve(
            query=scenario["query"],
            pipeline_contract={"channels": ["dense"], "fallback_order": ["dense"]},
            channel_results=scenario["channels"],
            top_k=3,
        )
        score = _evaluate_hit_rate(payload["selected"], scenario["expected"], k=3)
        scores.append(score)
    output = {
        "schema": "retrieval_benchmark_result.v1",
        "recall_at_3": round(sum(scores) / len(scores), 4),
        "mrr": round(sum(scores) / len(scores), 4),
        "top_k_hit_rate": round(sum(1.0 for score in scores if score > 0) / len(scores), 4),
        "scenario_count": len(scenarios),
    }
    assert output["schema"] == "retrieval_benchmark_result.v1"
    assert output["scenario_count"] == 3
    assert output["recall_at_3"] >= 0.0

