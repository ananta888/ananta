from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from agent.services.mail_account_service import MailAccountService
from agent.services.mail_contract_service import MailAccountV2, MailMessageMetadata, MailMessageRefV2, stable_mail_ref_id
from agent.services.mail_metadata_store_service import MailMetadataStore
from agent.services.mail_provider_ports import (
    MailContentAccessDecision,
    MailContentAccessRequest,
    MailContentAccessVerifier,
    MailProviderResult,
    MailSyncCursor,
)
from agent.services.mail_search_service import search_mail_metadata


class _Allow:
    def authorize(self, request):
        return MailProviderResult.success(
            MailContentAccessDecision(True, "allowed", "policy-1", "2030-01-01T00:00:00Z", "nonce")
        )


def _access(mail_ref_id: str):
    return MailContentAccessVerifier(
        policy=_Allow(), now=lambda: datetime(2029, 1, 1, tzinfo=UTC)
    ).verify(
        MailContentAccessRequest(
            account_id="a",
            workspace_id="w",
            artifact_ref=f"mail://{mail_ref_id}?scope=body_excerpt",
            mail_ref_id=mail_ref_id,
            grant_ref="grant",
            release_scope="body_excerpt",
        )
    ).value


def test_account_service_is_v2_only_and_does_not_migrate_on_read(tmp_path) -> None:
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps({"schema": "imap_accounts.v1", "accounts": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="schema_unsupported"):
        MailAccountService(store_path=path).list_accounts()
    path.unlink()
    service = MailAccountService(store_path=path)
    account = MailAccountV2(
        account_id="a",
        display_name="A",
        requested_protocol="imap",
        resolved_protocol="imap",
        username_ref="user://a",
        credential_ref="secret://a",
        sync_policy="manual",
        enabled=True,
        provider_config={"imap": {"host": "imap.example.test", "port": 993}},
    )
    service.create_account(account)
    assert service.get_account("a") == account


def test_metadata_store_uses_opaque_id_and_requires_verified_access_for_body(tmp_path) -> None:
    store = MailMetadataStore(store_path=tmp_path / "metadata.json")
    mail_ref_id = stable_mail_ref_id(account_id="a", protocol="imap", stable_identity="one")
    ref = MailMessageRefV2(mail_ref_id, "a", "imap", {"mailbox": "INBOX", "uid": 1}, 1)
    store.upsert_message(message_ref=ref, metadata=MailMessageMetadata(subject="Build", keywords=()))
    with pytest.raises(PermissionError):
        store.store_body(mail_ref_id=mail_ref_id, text_body="secret", html_body="", access=None)  # type: ignore[arg-type]
    store.store_body(mail_ref_id=mail_ref_id, text_body="visible", html_body="", access=_access(mail_ref_id))
    result = search_mail_metadata(store=store, filters={"subject": "build"})
    assert result["results"][0]["source_ref"] == f"mail://{mail_ref_id}"
    assert "INBOX" not in result["results"][0]["source_ref"]
    cursor = MailSyncCursor(account_id="a", protocol="imap", email_state="s1")
    store.save_sync_cursor(cursor)
    assert store.get_sync_cursor(account_id="a", protocol="imap") == cursor
