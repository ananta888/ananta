from __future__ import annotations

from agent.services.retrieval_policy_filter_service import get_retrieval_policy_filter_service


def test_security_retrieval_benchmark_outputs_isolation_metrics() -> None:
    service = get_retrieval_policy_filter_service()
    scenarios = [
        {
            "name": "allow_public_repo",
            "scope": "external_cloud_allowed",
            "chunks": [
                {
                    "engine": "repository_map",
                    "source": "agent/services/retrieval_service.py",
                    "content": "public-safe content",
                    "score": 1.0,
                    "metadata": {"source_type": "repo", "sensitivity": "public"},
                }
            ],
            "expected_allowed": 1,
            "expected_denied": 0,
        },
        {
            "name": "deny_unknown_sensitivity",
            "scope": "external_cloud_allowed",
            "chunks": [
                {
                    "engine": "knowledge_index",
                    "source": "docs/internal.md",
                    "content": "unknown metadata",
                    "score": 1.0,
                    "metadata": {"source_type": "artifact", "sensitivity": ""},
                }
            ],
            "expected_allowed": 0,
            "expected_denied": 1,
        },
        {
            "name": "segregate_mixed_sources",
            "scope": "external_cloud_allowed",
            "chunks": [
                {
                    "engine": "repository_map",
                    "source": "src/main.py",
                    "content": "repo code",
                    "score": 2.0,
                    "metadata": {"source_type": "repo", "sensitivity": "public"},
                },
                {
                    "engine": "result_memory",
                    "source": "memory:t-1",
                    "content": "memory context",
                    "score": 1.8,
                    "metadata": {"source_type": "task_memory", "sensitivity": "public"},
                },
            ],
            "expected_allowed": 1,
            "expected_denied": 1,
        },
    ]

    totals = {"allowed": 0, "denied": 0}
    scores: list[float] = []
    for scenario in scenarios:
        filtered, diagnostics = service.apply_filter(
            chunks=list(scenario["chunks"]),
            llm_scope=scenario["scope"],
            policy_mode="standard",
        )
        allowed = len(filtered)
        denied = int(diagnostics.get("denied_count") or 0)
        totals["allowed"] += allowed
        totals["denied"] += denied
        expected_allowed = int(scenario["expected_allowed"])
        expected_denied = int(scenario["expected_denied"])
        pass_score = 1.0 if (allowed == expected_allowed and denied == expected_denied) else 0.0
        scores.append(pass_score)

    output = {
        "schema": "security_retrieval_benchmark_result.v1",
        "scenario_count": len(scenarios),
        "isolation_pass_rate": round(sum(scores) / len(scores), 4),
        "total_allowed": totals["allowed"],
        "total_denied": totals["denied"],
    }
    assert output["schema"] == "security_retrieval_benchmark_result.v1"
    assert output["scenario_count"] == 3
    assert output["isolation_pass_rate"] == 1.0
