from __future__ import annotations

from agent.services.retrieval_source_contract import normalize_chunk_metadata


def test_normalize_chunk_metadata_adds_security_metadata_defaults() -> None:
    payload = normalize_chunk_metadata(
        engine="repository_map",
        source="agent/services/retrieval_service.py",
        content="retrieval context",
        metadata={},
    )

    security = dict(payload.get("security_metadata") or {})
    assert security.get("classification") == "internal"
    assert security.get("source_origin") == "repo"
    assert security.get("sensitivity") == "internal_low"
    assert security.get("tenancy") == "single_tenant"
    assert security.get("approval_class") == "standard"
    assert "chunk_security_tags" in security
    assert payload.get("classification") == security.get("classification")
    assert payload.get("sensitivity") == security.get("sensitivity")


def test_normalize_chunk_metadata_preserves_chunk_level_security_tags() -> None:
    payload = normalize_chunk_metadata(
        engine="knowledge_index",
        source="docs/wiki/payment.md",
        content="payment wiki context",
        metadata={
            "source_scope": "wiki",
            "classification": "restricted",
            "source_origin": "wiki",
            "sensitivity": "internal_high",
            "tenancy": "tenant_alpha",
            "approval_class": "operator_review",
            "chunk_security_tags": ["tenant:alpha", "scope:payments", "restricted"],
        },
    )

    security = dict(payload.get("security_metadata") or {})
    assert security.get("classification") == "restricted"
    assert security.get("source_origin") == "wiki"
    assert security.get("sensitivity") == "internal_high"
    assert security.get("tenancy") == "tenant_alpha"
    assert security.get("approval_class") == "operator_review"
    assert security.get("chunk_security_tags") == ["tenant:alpha", "scope:payments", "restricted"]
    assert payload.get("chunk_security_tags") == ["tenant:alpha", "scope:payments", "restricted"]
