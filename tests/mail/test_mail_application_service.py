from __future__ import annotations

from pathlib import Path

import pytest

from agent.services.mail_account_service import MailAccountService
from agent.services.mail_application_service import (
    MailApplicationError,
    MailApplicationService,
    NullLegacyMailBridge,
)
from agent.services.mail_contract_service import (
    MailMessageMetadata,
    MailMessageRefV2,
    stable_mail_ref_id,
)
from agent.services.mail_metadata_store_service import MailMetadataStore


def _service(tmp_path: Path) -> tuple[MailApplicationService, MailMetadataStore]:
    metadata = MailMetadataStore(store_path=tmp_path / "metadata.json")
    return (
        MailApplicationService(
            account_service=MailAccountService(
                store_path=tmp_path / "accounts.json"
            ),
            metadata_store=metadata,
            legacy=NullLegacyMailBridge(),
            task_service_factory=lambda: None,
        ),
        metadata,
    )


def test_account_preview_requires_discovery_before_auto_confirmation(
    tmp_path: Path,
) -> None:
    service, _metadata = _service(tmp_path)
    preview = service.preview_account(
        account_id="primary",
        display_name="Primary",
        requested_protocol="auto",
        username_ref="secret://mail/primary/username",
        credential_ref="secret://mail/primary/password",
        sync_policy="manual",
        provider_config={"session_url": "https://mail.test/.well-known/jmap"},
    )

    assert preview["account"]["requested_protocol"] == "auto"
    assert "credential_ref" not in preview["account"]
    assert "username_ref" not in preview["account"]
    with pytest.raises(
        MailApplicationError,
        match="mail_discovery_confirmation_required",
    ):
        service.confirm_account(preview=preview["draft"])

    account = service.confirm_account(
        preview=preview["draft"],
        resolved_protocol="jmap",
        discovery_task_id="mail-task-discovery",
    )
    assert account["resolved_protocol"] == "jmap"
    assert account["enabled"] is True


def test_metadata_is_public_but_body_requires_message_bound_capability(
    tmp_path: Path,
) -> None:
    service, metadata = _service(tmp_path)
    mail_ref_id = stable_mail_ref_id(
        account_id="primary",
        protocol="jmap",
        stable_identity={"email_id": "M1"},
    )
    metadata.upsert_message(
        message_ref=MailMessageRefV2(
            mail_ref_id=mail_ref_id,
            account_id="primary",
            protocol="jmap",
            protocol_locator={"email_id": "M1"},
            locator_version=1,
            thread_ref_id="thread-1",
        ),
        metadata=MailMessageMetadata(
            message_id_header="<one@example.test>",
            subject="Metadata only",
            from_address="sender@example.test",
        ),
    )
    access = service.authorize_operator_content(
        mail_ref_id=mail_ref_id,
        account_id="primary",
        workspace_id="repo",
        artifact_ref=f"mail://{mail_ref_id}",
        grant_ref="grant:operator:test",
        release_scope="full_body",
        explicit_confirmation=True,
    )
    metadata.store_body(
        mail_ref_id=mail_ref_id,
        text_body="classified body",
        html_body="",
        access=access,
    )

    rows = service.list_message_metadata()
    assert rows[0]["mail_ref_id"] == mail_ref_id
    assert "protocol_locator" not in rows[0]
    assert "body" not in rows[0]
    assert service.load_body(mail_ref_id, access=access)["text"] == "classified body"

    with pytest.raises(
        MailApplicationError,
        match="mail_content_confirmation_required",
    ):
        service.authorize_operator_content(
            mail_ref_id=mail_ref_id,
            account_id="primary",
            workspace_id="repo",
            artifact_ref=f"mail://{mail_ref_id}",
            grant_ref="grant:operator:test",
            release_scope="full_body",
            explicit_confirmation=False,
        )


def test_content_capability_cannot_be_reused_for_another_message(
    tmp_path: Path,
) -> None:
    service, metadata = _service(tmp_path)
    refs = [
        stable_mail_ref_id(
            account_id="primary",
            protocol="jmap",
            stable_identity={"email_id": provider_id},
        )
        for provider_id in ("M1", "M2")
    ]
    for index, mail_ref_id in enumerate(refs):
        metadata.upsert_message(
            message_ref=MailMessageRefV2(
                mail_ref_id=mail_ref_id,
                account_id="primary",
                protocol="jmap",
                protocol_locator={"email_id": f"M{index + 1}"},
                locator_version=1,
            ),
            metadata=MailMessageMetadata(subject=f"Message {index + 1}"),
        )
    access = service.authorize_operator_content(
        mail_ref_id=refs[0],
        account_id="primary",
        workspace_id="repo",
        artifact_ref=f"mail://{refs[0]}",
        grant_ref="grant:operator:test",
        release_scope="full_body",
        explicit_confirmation=True,
    )

    with pytest.raises(
        MailApplicationError,
        match="mail_content_access_scope_mismatch",
    ):
        service.load_body(refs[1], access=access)
