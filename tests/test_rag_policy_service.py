from __future__ import annotations

from agent.services.rag_policy_service import (
    is_chunk_allowed_for_scope,
    normalize_llm_scope,
    normalize_sensitivity,
)


def test_normalize_defaults_apply_secure_fallbacks() -> None:
    assert normalize_sensitivity("unknown-class") == "internal_medium"
    assert normalize_llm_scope("mystery") == "external_cloud_allowed"


def test_external_scope_blocks_sensitive_and_secret_chunks() -> None:
    blocked, reason = is_chunk_allowed_for_scope(
        chunk={"metadata": {"sensitivity": "internal_high"}},
        llm_scope="external_cloud_allowed",
    )
    assert blocked is False
    assert reason.startswith("sensitivity_blocked")

    blocked, reason = is_chunk_allowed_for_scope(
        chunk={"metadata": {"sensitivity": "public", "contains_secrets": True}},
        llm_scope="external_cloud_allowed",
    )
    assert blocked is False
    assert reason == "secret_or_customer_data_blocked_for_cloud_scope"


def test_local_scope_allows_sensitive_chunks() -> None:
    allowed, reason = is_chunk_allowed_for_scope(
        chunk={"metadata": {"sensitivity": "confidential", "contains_secrets": True}},
        llm_scope="local_only",
    )
    assert allowed is True
    assert reason == "allowed"
