from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent.services.mail_contract_service import (
    MailAccountV2,
    MailMessageMetadata,
    MailMessageRefV2,
    stable_mail_ref_id,
    validate_mail_account,
)
from agent.services.mail_provider_ports import (
    MailContentAccessDecision,
    MailContentAccessRequest,
    MailContentAccessVerifier,
    MailProviderResult,
    VerifiedMailContentAccess,
)


def _account() -> MailAccountV2:
    return MailAccountV2.from_mapping(
        {
            "account_id": "mail-a",
            "display_name": "A",
            "requested_protocol": "auto",
            "resolved_protocol": "jmap",
            "username_ref": "user://a",
            "credential_ref": "secret://mail/a",
            "sync_policy": "headers_only",
            "enabled": True,
            "provider_config": {"jmap": {"discovery_domain": "example.test"}},
        }
    )


def test_mail_account_v2_separates_requested_and_resolved_protocol_and_rejects_nested_secrets() -> None:
    account = _account()
    assert account.requested_protocol == "auto"
    assert account.effective_protocol == "jmap"
    payload = account.to_dict()
    payload["provider_config"]["jmap"]["session_token"] = "clear"
    assert validate_mail_account(payload)[0]["reason_code"] == "plaintext_credentials_forbidden"


def test_mail_message_ref_contains_only_identity_and_metadata_is_separate() -> None:
    ref = MailMessageRefV2(
        mail_ref_id=stable_mail_ref_id(account_id="a", protocol="jmap", stable_identity="email-1"),
        account_id="a",
        protocol="jmap",
        protocol_locator={"email_id": "email-1"},
        locator_version=1,
    )
    assert "subject" not in ref.to_dict()
    metadata = MailMessageMetadata(subject="Build", from_address="ci@example.test")
    assert metadata.to_dict()["subject"] == "Build"


class _Allow:
    def authorize(self, request: MailContentAccessRequest) -> MailProviderResult[MailContentAccessDecision]:
        return MailProviderResult.success(
            MailContentAccessDecision(
                allowed=True,
                reason_code="allowed",
                policy_decision_ref="policy-1",
                expires_at="2030-01-01T00:00:00Z",
                nonce="nonce-1",
            )
        )


def test_verified_content_access_can_only_be_issued_by_verifier() -> None:
    with pytest.raises(TypeError):
        VerifiedMailContentAccess(
            account_id="a",
            workspace_id="w",
            artifact_ref="mail://mailref-a?scope=body_excerpt",
            mail_ref_id="mailref-a",
            grant_ref="grant-1",
            release_scope="body_excerpt",
            policy_decision_ref="policy-1",
            expires_at="2030-01-01T00:00:00Z",
            nonce="n",
            _issuer=object(),
        )
    verifier = MailContentAccessVerifier(policy=_Allow(), now=lambda: datetime(2029, 1, 1, tzinfo=UTC))
    result = verifier.verify(
        MailContentAccessRequest(
            account_id="a",
            workspace_id="w",
            artifact_ref="mail://mailref-a?scope=body_excerpt",
            mail_ref_id="mailref-a",
            grant_ref="grant-1",
            release_scope="body_excerpt",
        )
    )
    assert result.ok is True
    assert isinstance(result.value, VerifiedMailContentAccess)
    assert result.value.mail_ref_id == "mailref-a"


def test_content_access_rejects_artifact_bound_to_another_message() -> None:
    verifier = MailContentAccessVerifier(policy=_Allow(), now=lambda: datetime(2029, 1, 1, tzinfo=UTC))
    result = verifier.verify(
        MailContentAccessRequest(
            account_id="a",
            workspace_id="w",
            artifact_ref="mail://mailref-other?scope=body_excerpt",
            mail_ref_id="mailref-a",
            grant_ref="grant-1",
            release_scope="body_excerpt",
        )
    )
    assert result.reason_code == "mail_content_artifact_mismatch"
