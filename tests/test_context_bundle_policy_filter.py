from __future__ import annotations

from agent.services.context_bundle_service import get_context_bundle_service


def test_context_bundle_filters_sensitive_chunks_for_external_scope() -> None:
    service = get_context_bundle_service()
    payload = {
        "query": "inspect code",
        "strategy": {},
        "policy_version": "v1",
        "chunks": [
            {
                "engine": "knowledge_index",
                "source": "public.md",
                "content": "public information",
                "score": 1.0,
                "metadata": {"sensitivity": "public"},
            },
            {
                "engine": "knowledge_index",
                "source": "internal.md",
                "content": "internal details",
                "score": 0.9,
                "metadata": {"sensitivity": "internal_high"},
            },
        ],
        "context_text": "mixed context",
        "token_estimate": 12,
    }

    bundle = service.build_bundle(
        query="inspect code",
        context_payload=payload,
        policy_mode="standard",
        llm_scope="external_cloud_allowed",
    )

    assert bundle["chunk_count"] == 1
    assert len(bundle["chunks"]) == 1
    assert (bundle["policy_filter"] or {}).get("denied_count") == 1
    assert (bundle["context_policy"] or {}).get("default_deny") is True
    assert (bundle["context_policy"] or {}).get("llm_scope") == "external_cloud_allowed"
