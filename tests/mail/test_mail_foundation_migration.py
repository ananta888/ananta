from __future__ import annotations

import json
from pathlib import Path

from agent.services.mail_account_service import MailAccountService
from agent.services.mail_metadata_store_service import MailMetadataStore
from agent.services.mail_migration_service import MailMigrationCommand, MailMigrationService


def _legacy(tmp_path: Path) -> tuple[Path, Path]:
    accounts = tmp_path / "data" / "imap" / "accounts.json"
    metadata = tmp_path / "data" / "imap" / "metadata.json"
    accounts.parent.mkdir(parents=True)
    accounts.write_text(
        json.dumps(
            {
                "schema": "imap_accounts.v1",
                "accounts": [
                    {
                        "account_id": "imap-a",
                        "display_name": "A",
                        "host": "imap.example.test",
                        "port": 993,
                        "username_ref": "user://a",
                        "credential_ref": "secret://imap/a",
                        "auth_mode": "password_app_token",
                        "tls_mode": "require_tls",
                        "sync_policy": "headers_only",
                        "enabled": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    metadata.write_text(
        json.dumps(
            {
                "schema": "imap_metadata_store.v1",
                "messages": [
                    {
                        "message_ref": {
                            "account_id": "imap-a",
                            "mailbox": "INBOX",
                            "uid": 1,
                            "message_id": "<one@example.test>",
                            "date": "2026-01-01T00:00:00Z",
                            "from": "a@example.test",
                            "to": "b@example.test",
                            "subject_hash": "hash",
                        },
                        "header_meta": {"subject": "One"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return accounts, metadata


def test_explicit_migration_is_dry_runnable_idempotent_and_preserves_credential_ref(tmp_path) -> None:
    legacy_accounts, legacy_metadata = _legacy(tmp_path)
    command = MailMigrationCommand(
        command_id="cmd-1",
        legacy_accounts_path=legacy_accounts,
        legacy_metadata_path=legacy_metadata,
        target_accounts_path=tmp_path / "data" / "mail" / "accounts.json",
        target_metadata_path=tmp_path / "data" / "mail" / "metadata.json",
        journal_path=tmp_path / "data" / "mail" / "migration-journal.json",
        dry_run=True,
    )
    dry = MailMigrationService().execute(command)
    assert dry.status == "dry_run"
    assert not command.target_accounts_path.exists()
    applied = MailMigrationService().execute(
        MailMigrationCommand(
            command_id=command.command_id,
            legacy_accounts_path=command.legacy_accounts_path,
            legacy_metadata_path=command.legacy_metadata_path,
            target_accounts_path=command.target_accounts_path,
            target_metadata_path=command.target_metadata_path,
            journal_path=command.journal_path,
            dry_run=False,
        )
    )
    assert applied.status == "complete"
    assert MailAccountService(store_path=command.target_accounts_path).get_account("imap-a").credential_ref == "secret://imap/a"
    first_ref = MailMetadataStore(store_path=command.target_metadata_path).list_messages()[0]["message_ref"]["mail_ref_id"]
    repeated = MailMigrationService().execute(
        MailMigrationCommand(
            command_id=command.command_id,
            legacy_accounts_path=command.legacy_accounts_path,
            legacy_metadata_path=command.legacy_metadata_path,
            target_accounts_path=command.target_accounts_path,
            target_metadata_path=command.target_metadata_path,
            journal_path=command.journal_path,
            dry_run=False,
        )
    )
    assert repeated.reason_code == "already_complete"
    assert MailMetadataStore(store_path=command.target_metadata_path).list_messages()[0]["message_ref"]["mail_ref_id"] == first_ref
    assert Path(applied.backup_dir).exists()
