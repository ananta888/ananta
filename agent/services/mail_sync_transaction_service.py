from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agent.services.mail_provider_ports import MailProviderResult, MailSyncDelta
from agent.services.mail_sync_state_store import (
    JmapSyncCheckpoint,
    MailSyncStateStoreError,
    sync_transaction_id,
)


@dataclass(frozen=True, slots=True)
class MailSyncTransactionRequest:
    transaction_id: str
    expected_revision: int
    checkpoint: JmapSyncCheckpoint
    delta: MailSyncDelta
    replace_scope: bool


class MailSyncUnitOfWork(Protocol):
    """One backend transaction containing metadata and checkpoint changes."""

    def apply_metadata(
        self,
        *,
        delta: MailSyncDelta,
        replace_scope: bool,
    ) -> MailProviderResult[None]:
        ...

    def stage_checkpoint(
        self,
        *,
        checkpoint: JmapSyncCheckpoint,
    ) -> MailProviderResult[None]:
        ...

    def commit(self) -> MailProviderResult[None]:
        ...

    def rollback(self) -> None:
        ...


class MailSyncTransactionPort(Protocol):
    def load_checkpoint(
        self,
        *,
        account_id: str,
        provider_account_id: str,
        scope: str,
        query_fingerprint: str,
    ) -> MailProviderResult[JmapSyncCheckpoint | None]:
        ...

    def begin(
        self,
        request: MailSyncTransactionRequest,
    ) -> MailProviderResult[MailSyncUnitOfWork]:
        """Must acquire/check expected_revision inside the backend transaction."""
        ...


class TransactionalMailSyncStateStore:
    """MailSyncStateStore adapter backed by a real rollback-capable unit of work."""

    def __init__(self, *, transaction_port: MailSyncTransactionPort) -> None:
        self._transaction_port = transaction_port

    def load(
        self,
        *,
        account_id: str,
        provider_account_id: str,
        scope: str,
        query_fingerprint: str,
    ) -> JmapSyncCheckpoint | None:
        try:
            result = self._transaction_port.load_checkpoint(
                account_id=account_id,
                provider_account_id=provider_account_id,
                scope=scope,
                query_fingerprint=query_fingerprint,
            )
        except Exception as exc:
            raise MailSyncStateStoreError("mail_sync_transaction_unavailable") from exc
        if not result.ok:
            raise MailSyncStateStoreError(result.reason_code)
        return result.value

    def apply_and_commit(
        self,
        *,
        expected_revision: int,
        checkpoint: JmapSyncCheckpoint,
        delta: MailSyncDelta,
        replace_scope: bool = False,
    ) -> bool:
        request = MailSyncTransactionRequest(
            transaction_id=sync_transaction_id(
                checkpoint=checkpoint,
                delta=delta,
            ),
            expected_revision=int(expected_revision),
            checkpoint=checkpoint,
            delta=delta,
            replace_scope=bool(replace_scope),
        )
        try:
            begun = self._transaction_port.begin(request)
        except Exception as exc:
            raise MailSyncStateStoreError("mail_sync_transaction_unavailable") from exc
        if not begun.ok or begun.value is None:
            if begun.reason_code == "mail_sync_concurrent_update":
                return False
            raise MailSyncStateStoreError(begun.reason_code)
        transaction = begun.value
        try:
            applied = transaction.apply_metadata(
                delta=delta,
                replace_scope=bool(replace_scope),
            )
            if not applied.ok:
                raise MailSyncStateStoreError(applied.reason_code)
            staged = transaction.stage_checkpoint(checkpoint=checkpoint)
            if not staged.ok:
                raise MailSyncStateStoreError(staged.reason_code)
            committed = transaction.commit()
            if not committed.ok:
                raise MailSyncStateStoreError(committed.reason_code)
        except Exception as exc:
            try:
                transaction.rollback()
            except Exception:
                pass
            if isinstance(exc, MailSyncStateStoreError):
                raise
            raise MailSyncStateStoreError("mail_sync_transaction_failed") from exc
        return True


__all__ = [
    "MailSyncTransactionPort",
    "MailSyncTransactionRequest",
    "MailSyncUnitOfWork",
    "TransactionalMailSyncStateStore",
]
