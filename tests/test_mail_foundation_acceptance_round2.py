from __future__ import annotations

import json
import multiprocessing
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agent.services.mail_contract_service import MailMessageMetadata, MailMessageRefV2, stable_mail_ref_id
from agent.services.mail_legacy_mapper import LegacyMailRecord, MailLegacyMapper
from agent.services.mail_metadata_store_service import MailMetadataStore
from agent.services.mail_migration_journal import MailFileLock, MailMultiFileTransaction
from agent.services.mail_migration_service import MailMigrationCommand, MailMigrationService


def _record(*, uid: int, message_id: str = "", content_hash: str = "", size: int | None = None) -> LegacyMailRecord:
    ref = MailMessageRefV2(
        stable_mail_ref_id(account_id="a", protocol="imap", stable_identity=f"INBOX:{uid}"),
        "a",
        "imap",
        {"mailbox": "INBOX", "uid": uid},
        1,
    )
    return LegacyMailRecord(
        ref,
        MailMessageMetadata(
            message_id_header=message_id,
            date="2026-01-01T00:00:00Z",
            content_hash=content_hash,
            size=size,
        ),
    )


def test_dedupe_is_conservative_for_missing_and_duplicate_message_ids() -> None:
    existing = [_record(uid=1, message_id="<same>", content_hash="h", size=10)]
    assert MailLegacyMapper.classify_match(
        _record(uid=2, message_id="", content_hash="h", size=10), existing
    ).outcome == "unmatched"
    duplicate = existing + [_record(uid=3, message_id="<same>", content_hash="h", size=10)]
    assert MailLegacyMapper.classify_match(
        _record(uid=4, message_id="<same>", content_hash="h", size=10), duplicate
    ).outcome == "ambiguous"
    assert MailLegacyMapper.classify_match(
        _record(uid=5, message_id="<same>", content_hash="h", size=10), existing
    ).outcome == "matched"


def test_locator_alias_table_is_persisted_versioned_and_conflict_safe(tmp_path: Path) -> None:
    store = MailMetadataStore(store_path=tmp_path / "metadata.json")
    first = _record(uid=1)
    store.upsert_message(message_ref=first.message_ref, metadata=first.metadata)
    aliases = store.list_locator_aliases(mail_ref_id=first.message_ref.mail_ref_id)
    assert aliases[0].alias_version >= 1
    assert store.resolve_locator(
        account_id="a",
        protocol="imap",
        protocol_locator={"mailbox": "INBOX", "uid": 1},
        locator_version=1,
    ) == first.message_ref.mail_ref_id
    conflicting = MailMessageRefV2(
        stable_mail_ref_id(account_id="a", protocol="imap", stable_identity="different"),
        "a",
        "imap",
        {"mailbox": "INBOX", "uid": 1},
        1,
    )
    with pytest.raises(ValueError, match="alias_conflict"):
        store.upsert_message(message_ref=conflicting, metadata=MailMessageMetadata())


def test_lock_contention_and_multi_file_failure_restore_all_preimages(tmp_path: Path) -> None:
    lock_path = tmp_path / "global.lock"

    def contend() -> str:
        try:
            with MailFileLock(path=lock_path, timeout_seconds=0.02):
                return "acquired"
        except TimeoutError:
            return "timeout"

    with MailFileLock(path=lock_path):
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(contend).result() == "timeout"
    one = tmp_path / "one.json"
    two = tmp_path / "two.json"
    one.write_text('{"before": 1}\n', encoding="utf-8")
    two.write_text('{"before": 2}\n', encoding="utf-8")
    calls = 0

    def failing_replace(staged: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated_crash")
        staged.replace(target)

    transaction = MailMultiFileTransaction(
        transaction_root=tmp_path / "transactions",
        transaction_id="tx",
        replace_target=failing_replace,
    )
    with pytest.raises(OSError, match="simulated_crash"):
        transaction.commit({one: {"after": 1}, two: {"after": 2}})
    assert json.loads(one.read_text(encoding="utf-8")) == {"before": 1}
    assert json.loads(two.read_text(encoding="utf-8")) == {"before": 2}


def test_process_crash_releases_lock_and_wal_recovers_partial_commit(tmp_path: Path) -> None:
    context = multiprocessing.get_context("fork")
    lock_path = tmp_path / "crash.lock"
    ready = context.Event()

    def crash_with_lock() -> None:
        with MailFileLock(path=lock_path):
            ready.set()
            os._exit(9)

    locker = context.Process(target=crash_with_lock)
    locker.start()
    assert ready.wait(timeout=2)
    locker.join(timeout=2)
    assert locker.exitcode == 9
    with MailFileLock(path=lock_path, timeout_seconds=0.2):
        pass

    one = tmp_path / "crash-one.json"
    two = tmp_path / "crash-two.json"
    one.write_text('{"before": 1}\n', encoding="utf-8")
    two.write_text('{"before": 2}\n', encoding="utf-8")
    transaction_root = tmp_path / "crash-transactions"

    def crash_mid_commit() -> None:
        calls = 0

        def replace_then_exit(staged: Path, target: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                os._exit(17)
            os.replace(staged, target)

        MailMultiFileTransaction(
            transaction_root=transaction_root,
            transaction_id="crash-tx",
            replace_target=replace_then_exit,
        ).commit({one: {"after": 1}, two: {"after": 2}})

    writer = context.Process(target=crash_mid_commit)
    writer.start()
    writer.join(timeout=3)
    assert writer.exitcode == 17
    recovery = MailMultiFileTransaction(
        transaction_root=transaction_root,
        transaction_id="crash-tx",
    )
    assert recovery.recover_if_needed() is True
    assert json.loads(one.read_text(encoding="utf-8")) == {"before": 1}
    assert json.loads(two.read_text(encoding="utf-8")) == {"before": 2}


def _legacy_command(tmp_path: Path) -> MailMigrationCommand:
    legacy_root = tmp_path / "data" / "imap"
    legacy_root.mkdir(parents=True)
    accounts = legacy_root / "accounts.json"
    metadata = legacy_root / "metadata.json"
    accounts.write_text(
        json.dumps(
            {
                "schema": "imap_accounts.v1",
                "accounts": [
                    {
                        "account_id": "a",
                        "display_name": "A",
                        "host": "imap.example.test",
                        "port": 993,
                        "username_ref": "user://a",
                        "credential_ref": "secret://a",
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
                            "account_id": "a",
                            "mailbox": "INBOX",
                            "uid": 1,
                            "message_id": "<one>",
                            "date": "2026-01-01T00:00:00Z",
                            "from": "a@example.test",
                            "to": "b@example.test",
                            "subject_hash": "s",
                            "size": 10,
                            "content_hash": "h",
                        },
                        "header_meta": {"subject": "One"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    root = tmp_path / "data" / "mail"
    return MailMigrationCommand(
        "cmd",
        accounts,
        metadata,
        root / "accounts.json",
        root / "metadata.json",
        root / "journal.json",
        False,
    )


def test_migration_hashes_alias_scope_and_real_restore(tmp_path: Path) -> None:
    command = _legacy_command(tmp_path)
    original_target = {"schema": "mail_accounts.v2", "accounts": []}
    command.target_accounts_path.parent.mkdir(parents=True)
    command.target_accounts_path.write_text(json.dumps(original_target), encoding="utf-8")
    report = MailMigrationService().execute(command)
    assert report.status == "complete"
    assert report.source_hashes == report.backup_hashes
    journal = json.loads(command.journal_path.read_text(encoding="utf-8"))
    assert journal["entries"][report.migration_id]["target_hashes"]
    assert report.alias_count >= 13
    grant_path = command.target_accounts_path.parent / "grant-aliases.json"
    aliases = json.loads(grant_path.read_text(encoding="utf-8"))
    assert any(item["legacy_artifact_ref"].endswith("scope=excerpt") for item in aliases["aliases"])
    restored = MailMigrationService().restore(
        command,
        migration_id=report.migration_id,
        approval_ref="approval-restore",
    )
    assert restored.status == "restored"
    assert json.loads(command.target_accounts_path.read_text(encoding="utf-8")) == original_target
    assert not command.target_metadata_path.exists()
    assert not grant_path.exists()


def test_restore_rejects_tampered_backup(tmp_path: Path) -> None:
    command = _legacy_command(tmp_path)
    command.target_accounts_path.parent.mkdir(parents=True)
    command.target_accounts_path.write_text(
        json.dumps({"schema": "mail_accounts.v2", "accounts": []}),
        encoding="utf-8",
    )
    report = MailMigrationService().execute(command)
    manifest = json.loads((Path(report.backup_dir) / "manifest.json").read_text(encoding="utf-8"))
    target = next(item for item in manifest["targets"].values() if item["existed"])
    Path(target["preimage"]).write_text("tampered", encoding="utf-8")
    restored = MailMigrationService().restore(
        command,
        migration_id=report.migration_id,
        approval_ref="approval",
    )
    assert restored.reason_code == "backup_content_hash_mismatch"
