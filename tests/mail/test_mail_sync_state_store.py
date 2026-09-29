from __future__ import annotations

import time

import portalocker
import pytest

from agent.services.mail_contract_service import (
    MailMessageMetadata,
    MailMessageRefV2,
    stable_mail_ref_id,
)
from agent.services.mail_provider_ports import MailMessage, MailProviderResult, MailSyncCursor, MailSyncDelta
from agent.services.mail_sync_state_store import (
    JmapSyncCheckpoint,
    MailSyncStateStoreError,
    PersistentMailSyncStateStore,
)


class _Applier:
    def __init__(self, *, fail_once: bool = False) -> None:
        self.fail_once = fail_once
        self.transactions = set()
        self.calls = 0

    def apply(self, **values):
        self.calls += 1
        if self.fail_once:
            self.fail_once = False
            return MailProviderResult(ok=False, reason_code="mail_metadata_temporarily_unavailable")
        self.transactions.add(values["transaction_id"])
        return MailProviderResult(ok=True, reason_code="ok")


def _checkpoint(revision: int = 1) -> JmapSyncCheckpoint:
    return JmapSyncCheckpoint(
        account_id="acc-1",
        provider_account_id="A1",
        scope="default",
        mailbox_state="m1",
        email_state="e1",
        query_state="q1",
        query_fingerprint="fingerprint-1",
        revision=revision,
    )


def _delta() -> MailSyncDelta:
    message_ref = MailMessageRefV2(
        mail_ref_id=stable_mail_ref_id(
            account_id="acc-1",
            protocol="jmap",
            stable_identity={"provider_account_id": "A1", "email_id": "E1"},
        ),
        account_id="acc-1",
        protocol="jmap",
        protocol_locator={"provider_account_id": "A1", "email_id": "E1"},
        locator_version=1,
    )
    return MailSyncDelta(
        cursor=MailSyncCursor(
            account_id="acc-1",
            protocol="jmap",
            scope="default",
            mailbox_state="m1",
            email_state="e1",
            query_state="q1",
        ),
        created=(
            MailMessage(
                message_ref=message_ref,
                metadata=MailMessageMetadata(subject="Metadata only"),
            ),
        ),
    )


def test_persistent_sync_store_cas_and_private_atomic_file(tmp_path) -> None:
    applier = _Applier()
    path = tmp_path / "sync-state.json"
    store = PersistentMailSyncStateStore(store_path=path, delta_applier=applier)
    assert store.apply_and_commit(
        expected_revision=0,
        checkpoint=_checkpoint(),
        delta=_delta(),
        replace_scope=True,
    )
    loaded = store.load(
        account_id="acc-1",
        provider_account_id="A1",
        scope="default",
        query_fingerprint="fingerprint-1",
    )
    assert loaded == _checkpoint()
    assert store.apply_and_commit(
        expected_revision=0,
        checkpoint=_checkpoint(2),
        delta=_delta(),
    ) is False
    assert path.stat().st_mode & 0o777 == 0o600


def test_pending_delta_is_replayed_idempotently_after_failure(tmp_path) -> None:
    path = tmp_path / "sync-state.json"
    failing = _Applier(fail_once=True)
    store = PersistentMailSyncStateStore(store_path=path, delta_applier=failing)
    with pytest.raises(MailSyncStateStoreError, match="mail_metadata_temporarily_unavailable"):
        store.apply_and_commit(
            expected_revision=0,
            checkpoint=_checkpoint(),
            delta=_delta(),
        )
    recovering = _Applier()
    recovered_store = PersistentMailSyncStateStore(store_path=path, delta_applier=recovering)
    loaded = recovered_store.load(
        account_id="acc-1",
        provider_account_id="A1",
        scope="default",
        query_fingerprint="fingerprint-1",
    )
    assert loaded == _checkpoint()
    assert recovering.calls == 1


def test_persistent_sync_store_times_out_under_lock_contention(tmp_path) -> None:
    path = tmp_path / "sync-state.json"
    lock_path = path.with_suffix(f"{path.suffix}.lock")
    store = PersistentMailSyncStateStore(
        store_path=path,
        delta_applier=_Applier(),
        lock_timeout_seconds=0.1,
    )
    with portalocker.Lock(
        str(lock_path),
        mode="a+",
        timeout=0,
        flags=portalocker.LOCK_EX | portalocker.LOCK_NB,
    ):
        started = time.monotonic()
        with pytest.raises(portalocker.exceptions.LockException):
            store.load(
                account_id="acc-1",
                provider_account_id="A1",
                scope="default",
                query_fingerprint="fingerprint-1",
            )
        assert time.monotonic() - started < 1.0
