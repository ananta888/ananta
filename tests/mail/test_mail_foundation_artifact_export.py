from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent.services.mail_artifact_service import MailArtifactService, mail_artifact_ref
from agent.services.mail_contract_service import MailMessageMetadata, MailMessageRefV2, stable_mail_ref_id
from agent.services.mail_export_service import MailExportService
from agent.services.mail_provider_ports import (
    MailContentAccessDecision,
    MailContentAccessRequest,
    MailContentAccessVerifier,
    MailProviderResult,
)


class _Allow:
    def authorize(self, request):
        return MailProviderResult.success(
            MailContentAccessDecision(True, "allowed", "policy", "2030-01-01T00:00:00Z", "nonce")
        )


def _fixture():
    mail_ref_id = stable_mail_ref_id(account_id="a", protocol="jmap", stable_identity="email-1")
    ref = MailMessageRefV2(mail_ref_id, "a", "jmap", {"email_id": "email-1"}, 1)
    artifact_ref = mail_artifact_ref(mail_ref_id, "body_excerpt")
    access = MailContentAccessVerifier(
        policy=_Allow(), now=lambda: datetime(2029, 1, 1, tzinfo=UTC)
    ).verify(
        MailContentAccessRequest(
            account_id="a",
            workspace_id="w",
            artifact_ref=artifact_ref,
            mail_ref_id=mail_ref_id,
            grant_ref="grant",
            release_scope="body_excerpt",
        )
    ).value
    return ref, access


def test_artifact_and_export_never_expose_provider_locator_and_body_requires_verified_access(tmp_path) -> None:
    ref, access = _fixture()
    service = MailArtifactService(store_path=tmp_path / "artifacts.json")
    metadata_artifact = service.register(
        message_ref=ref,
        scope="metadata_only",
        redaction_status="not_required",
        policy_decision_ref="policy",
    )
    assert "email_id" not in str(metadata_artifact)
    with pytest.raises(PermissionError):
        service.register(
            message_ref=ref,
            scope="body_excerpt",
            redaction_status="redacted",
            policy_decision_ref="policy",
            content="body",
        )
    body_artifact = service.register(
        message_ref=ref,
        scope="body_excerpt",
        redaction_status="redacted",
        policy_decision_ref="policy",
        content="body",
        access=access,
    )
    assert body_artifact["artifact_ref"] == access.artifact_ref
    with pytest.raises(PermissionError):
        MailExportService().export(
            message_ref=ref,
            metadata=MailMessageMetadata(subject="S"),
            body_text="secret",
            format_name="json",
            include_body=True,
            export_dir=tmp_path / "exports",
        )
    exported = MailExportService().export(
        message_ref=ref,
        metadata=MailMessageMetadata(subject="S"),
        body_text="redacted",
        format_name="json",
        include_body=True,
        export_dir=tmp_path / "exports",
        access=access,
    )
    assert exported["body_included"] is True
