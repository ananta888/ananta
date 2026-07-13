from __future__ import annotations

import pytest

from agent.services.retrieval_source_contract import (
    infer_source_id,
    normalize_chunk_metadata,
)


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
    assert payload.get("source_id") is None
    assert payload.get("source_id_verified") is False
    assert payload.get("source_id_verification") == {
        "status": "unverified",
        "reason_code": "source_id_missing",
        "verified": False,
    }
    assert payload.get("citation") == {
        "source_type": "repo",
        "source_id": None,
        "verification_status": "unverified",
        "reason_code": "source_id_missing",
        "path": "agent/services/retrieval_service.py",
    }


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
    assert payload.get("source_id") is None
    assert payload.get("source_id_verified") is False


@pytest.mark.parametrize(
    ("source_type", "source", "metadata"),
    [
        ("repo", "agent/services/retrieval_service.py", {}),
        ("repo", "unknown", {}),
        ("wiki", "docs/wiki/payment.md", {"article_title": "Payment retries"}),
        ("wiki", "docs/wiki/payment.md", {"wiki_article_title": "Payment retries"}),
        ("artifact", "unknown", {"source_id": "unknown"}),
    ],
)
def test_infer_source_id_never_synthesizes_identity_from_display_fields(
    source_type: str,
    source: str,
    metadata: dict[str, str],
) -> None:
    assert (
        infer_source_id(
            source_type=source_type,
            source=source,
            metadata=metadata,
        )
        == ""
    )


def test_unchecked_source_id_is_diagnostic_but_not_citable() -> None:
    payload = normalize_chunk_metadata(
        engine="knowledge_index",
        source="docs/runtime.md",
        content="runtime context",
        metadata={"source_scope": "artifact", "source_id": "SRC_9999"},
    )

    assert payload.get("source_id") == "SRC_9999"
    assert payload.get("source_id_verified") is False
    assert payload.get("source_id_verification") == {
        "status": "unverified",
        "reason_code": "source_id_unverified",
        "verified": False,
    }
    citation = dict(payload.get("citation") or {})
    assert citation.get("source_type") == "artifact"
    assert citation.get("source_id") is None
    assert citation.get("verification_status") == "unverified"
    assert citation.get("reason_code") == "source_id_unverified"


@pytest.mark.parametrize("source_id", ["SRC_0001", "RUN_0001"])
def test_catalog_allowlist_promotes_only_bound_source_id_to_citation(source_id: str) -> None:
    payload = normalize_chunk_metadata(
        engine="knowledge_index",
        source="docs/runtime.md",
        content="runtime context",
        metadata={"source_scope": "artifact", "source_id": source_id},
        verified_source_ids={source_id},
    )

    assert payload.get("source_id") == source_id
    assert payload.get("source_id_verified") is True
    assert payload.get("source_id_verification") == {
        "status": "verified",
        "reason_code": "source_id_verified",
        "verified": True,
    }
    assert (payload.get("citation") or {}).get("source_id") == source_id
    assert (payload.get("citation") or {}).get("verification_status") == "verified"


def test_self_asserted_verification_metadata_cannot_promote_source_id() -> None:
    payload = normalize_chunk_metadata(
        engine="knowledge_index",
        source="docs/runtime.md",
        content="runtime context",
        metadata={
            "source_scope": "artifact",
            "source_id": "SRC_FORGED",
            "source_id_verified": True,
            "source_id_verification": {"status": "verified", "verified": True},
        },
    )

    assert payload.get("source_id") == "SRC_FORGED"
    assert payload.get("source_id_verified") is False
    assert (payload.get("source_id_verification") or {}).get("reason_code") == "source_id_unverified"
    assert (payload.get("citation") or {}).get("source_id") is None


def test_non_catalog_id_format_stays_unverified_even_when_allowlisted() -> None:
    payload = normalize_chunk_metadata(
        engine="knowledge_index",
        source="docs/runtime.md",
        content="runtime context",
        metadata={"source_scope": "artifact", "source_id": "artifact-1"},
        verified_source_ids={"artifact-1"},
    )

    assert payload.get("source_id_verified") is False
    assert (payload.get("source_id_verification") or {}).get("reason_code") == "source_id_unverified"
    assert (payload.get("citation") or {}).get("source_id") is None
