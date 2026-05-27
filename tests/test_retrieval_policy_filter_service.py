from __future__ import annotations

from agent.services.retrieval_policy_filter_service import get_retrieval_policy_filter_service


def test_retrieval_policy_filter_denies_unknown_sensitivity_for_cloud_scopes() -> None:
    service = get_retrieval_policy_filter_service()
    chunks = [
        {
            "engine": "knowledge_index",
            "source": "docs/unknown.md",
            "content": "unknown sensitivity content",
            "score": 1.0,
            "metadata": {"source_type": "artifact", "sensitivity": ""},
        }
    ]

    filtered, diagnostics = service.apply_filter(
        chunks=chunks,
        llm_scope="external_cloud_allowed",
        policy_mode="standard",
    )

    assert filtered == []
    assert diagnostics["denied_count"] == 1
    assert diagnostics["denied_by_reason"]["unknown_sensitivity_default_deny"] == 1


def test_retrieval_policy_filter_can_downgrade_raw_disallowed_chunks() -> None:
    service = get_retrieval_policy_filter_service()
    chunks = [
        {
            "engine": "knowledge_index",
            "source": "docs/public.md",
            "content": "raw section",
            "score": 1.0,
            "metadata": {"source_type": "artifact", "sensitivity": "public", "raw_allowed": False},
        }
    ]

    filtered, diagnostics = service.apply_filter(
        chunks=chunks,
        llm_scope="external_cloud_allowed",
        policy_mode="standard",
    )

    assert len(filtered) == 1
    assert "[POLICY_DOWNGRADED]" in filtered[0]["content"]
    assert diagnostics["downgraded_count"] == 1
    assert diagnostics["downgraded_by_reason"]["raw_not_allowed_for_external_scope"] == 1


def test_retrieval_policy_filter_enforces_source_segregation() -> None:
    service = get_retrieval_policy_filter_service()
    chunks = [
        {
            "engine": "repository_map",
            "source": "agent/services/retrieval_service.py",
            "content": "repo context",
            "score": 2.0,
            "metadata": {"source_type": "repo", "sensitivity": "public"},
        },
        {
            "engine": "result_memory",
            "source": "memory:t1",
            "content": "task memory context",
            "score": 1.5,
            "metadata": {"source_type": "task_memory", "sensitivity": "public"},
        },
    ]

    filtered, diagnostics = service.apply_filter(
        chunks=chunks,
        llm_scope="external_cloud_allowed",
        policy_mode="standard",
    )

    assert len(filtered) == 1
    assert filtered[0]["metadata"]["source_type"] == "repo"
    assert diagnostics["segregation"]["applied"] is True
    assert diagnostics["denied_count"] == 1
    assert any(reason.startswith("source_segregation_blocked:task_memory") for reason in diagnostics["denied_by_reason"])
