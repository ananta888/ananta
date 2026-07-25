from __future__ import annotations

import multiprocessing
import os
from pathlib import Path

import pytest

from agent.services.mail_contract_service import (
    MailMessageMetadata,
    MailMessageRefV2,
)
from agent.services.mail_provider_ports import MailMessage, MailSyncCursor, MailSyncDelta
from agent.services.mail_sync_state_store import JmapSyncCheckpoint, MailSyncStateStoreError
from agent.services.mail_sync_transaction_service import MailSyncTransactionRequest
from worker.mail_sync_transaction_adapter import (
    SqliteMailMetadataStore,
    build_transactional_mail_runtime_state,
)
from worker.mail_task_composition import build_production_mail_task_execution


def _message(mail_ref_id: str = "mail-ref-1") -> MailMessage:
    return MailMessage(
        message_ref=MailMessageRefV2(
            mail_ref_id=mail_ref_id,
            account_id="account-1",
            protocol="jmap",
            protocol_locator={
                "provider_account_id": "provider-1",
                "email_id": f"email-{mail_ref_id}",
            },
            locator_version=1,
            thread_ref_id="thread-1",
        ),
        metadata=MailMessageMetadata(
            message_id_header=f"<{mail_ref_id}@example.test>",
            date="2025-01-14T09:00:00Z",
            from_address="alice@example.test",
            to_addresses=("bob@example.test",),
            subject="Atomic fixture",
            size=42,
            mailbox_ref_ids=("mailbox-inbox",),
            keywords=("$seen",),
            body_structure={"type": "text/plain"},
        ),
    )


def _delta(mail_ref_id: str = "mail-ref-1") -> MailSyncDelta:
    return MailSyncDelta(
        cursor=MailSyncCursor(
            account_id="account-1",
            protocol="jmap",
            scope="default",
            mailbox_state="mailbox-state-1",
            email_state="email-state-1",
            query_state="query-state-1",
        ),
        created=(_message(mail_ref_id),),
    )


def _checkpoint(revision: int = 1) -> JmapSyncCheckpoint:
    return JmapSyncCheckpoint(
        account_id="account-1",
        provider_account_id="provider-1",
        scope="default",
        mailbox_state="mailbox-state-1",
        email_state="email-state-1",
        query_state="query-state-1",
        query_fingerprint="query-fingerprint-1",
        revision=revision,
    )


def _crash_with_open_transaction(database_path: str) -> None:
    state = build_transactional_mail_runtime_state(database_path=database_path)
    request = MailSyncTransactionRequest(
        transaction_id="crash-fixture",
        expected_revision=0,
        checkpoint=_checkpoint(),
        delta=_delta(),
        replace_scope=True,
    )
    begun = state.transaction_port.begin(request)
    assert begun.ok and begun.value is not None
    assert begun.value.apply_metadata(
        delta=request.delta,
        replace_scope=True,
    ).ok
    os._exit(17)


def test_metadata_and_checkpoint_commit_in_one_transaction(tmp_path: Path) -> None:
    state = build_transactional_mail_runtime_state(
        database_path=tmp_path / "runtime.sqlite3"
    )

    assert state.sync_state_store.apply_and_commit(
        expected_revision=0,
        checkpoint=_checkpoint(),
        delta=_delta(),
        replace_scope=True,
    )

    row = state.metadata_store.get_by_mail_ref_id("mail-ref-1")
    assert row is not None
    assert row["message_ref"]["thread_ref_id"] == "thread-1"
    assert row["metadata"]["keywords"] == ["$seen"]
    loaded = state.sync_state_store.load(
        account_id="account-1",
        provider_account_id="provider-1",
        scope="default",
        query_fingerprint="query-fingerprint-1",
    )
    assert loaded == _checkpoint()


def test_failure_after_metadata_rolls_back_metadata_and_checkpoint(
    tmp_path: Path,
) -> None:
    def fail_after_metadata(stage: str) -> None:
        if stage == "after_metadata":
            raise RuntimeError("injected")

    state = build_transactional_mail_runtime_state(
        database_path=tmp_path / "runtime.sqlite3",
        fault_injector=fail_after_metadata,
    )

    with pytest.raises(MailSyncStateStoreError, match="mail_metadata_apply_failed"):
        state.sync_state_store.apply_and_commit(
            expected_revision=0,
            checkpoint=_checkpoint(),
            delta=_delta(),
            replace_scope=True,
        )

    assert state.metadata_store.get_by_mail_ref_id("mail-ref-1") is None
    assert state.sync_state_store.load(
        account_id="account-1",
        provider_account_id="provider-1",
        scope="default",
        query_fingerprint="query-fingerprint-1",
    ) is None


def test_process_loss_before_commit_leaves_no_partial_sync_state(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    process = multiprocessing.get_context("fork").Process(
        target=_crash_with_open_transaction,
        args=(str(database_path),),
    )

    process.start()
    process.join(timeout=15)

    assert process.exitcode == 17
    state = build_transactional_mail_runtime_state(database_path=database_path)
    assert state.metadata_store.get_by_mail_ref_id("mail-ref-1") is None
    assert state.sync_state_store.load(
        account_id="account-1",
        provider_account_id="provider-1",
        scope="default",
        query_fingerprint="query-fingerprint-1",
    ) is None


def test_scope_replacement_and_revision_cas_are_atomic(tmp_path: Path) -> None:
    state = build_transactional_mail_runtime_state(
        database_path=tmp_path / "runtime.sqlite3"
    )
    assert state.sync_state_store.apply_and_commit(
        expected_revision=0,
        checkpoint=_checkpoint(),
        delta=_delta("old-ref"),
        replace_scope=True,
    )

    replacement_checkpoint = _checkpoint(revision=2)
    assert state.sync_state_store.apply_and_commit(
        expected_revision=1,
        checkpoint=replacement_checkpoint,
        delta=_delta("new-ref"),
        replace_scope=True,
    )

    assert state.metadata_store.get_by_mail_ref_id("old-ref") is None
    assert state.metadata_store.get_by_mail_ref_id("new-ref") is not None
    assert (
        state.sync_state_store.apply_and_commit(
            expected_revision=1,
            checkpoint=_checkpoint(revision=3),
            delta=_delta("stale-ref"),
            replace_scope=True,
        )
        is False
    )
    assert state.metadata_store.get_by_mail_ref_id("stale-ref") is None


def test_production_default_handler_uses_transactional_sqlite_state(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "mail"

    handler = build_production_mail_task_execution(
        environ={
            "ANANTA_REPO_ROOT": str(tmp_path),
            "ANANTA_MAIL_DATA_ROOT": str(data_root),
            "ANANTA_MAIL_ENABLED": "1",
        }
    )

    assert callable(handler.execute)
    assert (data_root / "runtime-state-v1.sqlite3").is_file()
    assert not (data_root / "sync-state-v1.json").exists()
    assert not (data_root / "metadata-v2.json").exists()
    assert isinstance(
        SqliteMailMetadataStore(
            database_path=data_root / "runtime-state-v1.sqlite3"
        ),
        SqliteMailMetadataStore,
    )
