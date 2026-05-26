from __future__ import annotations

from agent.services.imap_security_policy_service import (
    decide_mail_release,
    default_mail_security_policy,
    mail_exposure_label,
    redact_mail_content,
)


def test_default_mail_security_policy_denies_worker_and_llm_by_default() -> None:
    policy = default_mail_security_policy()
    assert policy["default_worker_access"] == "denied"
    assert policy["default_llm_access"] == "denied"
    decision = decide_mail_release(policy=policy, requested_scope="full_body", target="cloud_worker")
    assert decision["allowed"] is False
    assert decision["reason_code"] == "cloud_context_denied"


def test_mail_release_requires_explicit_scope_allowance() -> None:
    policy = default_mail_security_policy() | {"allowed_scopes": ["metadata_only", "body_excerpt"]}
    blocked = decide_mail_release(policy=policy, requested_scope="full_body", target="local_worker")
    allowed = decide_mail_release(policy=policy, requested_scope="body_excerpt", target="local_worker")
    assert blocked["allowed"] is False
    assert blocked["reason_code"] == "scope_not_allowed"
    assert allowed["allowed"] is True
    assert allowed["scope_label"] == "body excerpt"


def test_mail_redaction_detects_secrets() -> None:
    result = redact_mail_content("password=hunter2 token=abc123")
    assert result["redaction_status"] == "redacted"
    assert result["redaction_hits"] >= 1
    assert "[REDACTED_SECRET]" in result["text"]


def test_mail_exposure_labels_cover_known_levels() -> None:
    assert mail_exposure_label("metadata_only") == "metadata only"
    assert mail_exposure_label("full_body") == "full body"
