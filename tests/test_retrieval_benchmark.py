from scripts.retrieval_benchmark import (
    RETRIEVAL_BENCHMARK_SCENARIOS,
    aggregate_scores,
    evaluate_payload_against_scenario,
)


def test_retrieval_benchmark_scenarios_cover_core_task_kinds():
    task_kinds = {item.get("task_kind") for item in RETRIEVAL_BENCHMARK_SCENARIOS}
    assert {"bugfix", "refactor", "architecture", "config"}.issubset(task_kinds)


def test_evaluate_payload_against_scenario_uses_fusion_metrics():
    scenario = RETRIEVAL_BENCHMARK_SCENARIOS[0]
    payload = {
        "chunks": [{"engine": "knowledge_index"} for _ in range(3)],
        "token_estimate": 120,
        "context_text": "timeout retry worker error handling",
        "budget": {"retrieval_utilization": 0.75},
        "strategy": {
            "fusion": {
                "dedupe": {"identity_duplicates": 1, "content_duplicates": 1},
                "candidate_counts": {"all": 10, "final": 4},
            }
        },
    }
    result = evaluate_payload_against_scenario(payload, scenario)

    assert result.scenario_id == scenario["id"]
    assert result.chunk_count == 3
    assert result.duplicate_rate == 0.2
    assert result.noise_rate == 0.6
    assert result.marker_coverage > 0
    assert 0 <= result.score <= 1


def test_aggregate_scores_groups_by_task_kind():
    scenario = RETRIEVAL_BENCHMARK_SCENARIOS[0]
    payload = {
        "chunks": [],
        "token_estimate": 1,
        "context_text": "timeout retry worker error",
        "budget": {"retrieval_utilization": 0.9},
        "strategy": {"fusion": {"dedupe": {}, "candidate_counts": {"all": 1, "final": 1}}},
    }
    score = evaluate_payload_against_scenario(payload, scenario)
    report = aggregate_scores([score])

    assert report["count"] == 1
    assert report["average_score"] == score.score
    assert report["by_task_kind"]["bugfix"] == score.score
