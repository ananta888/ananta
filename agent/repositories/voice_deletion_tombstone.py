from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import VoiceDeletionTombstoneDB
from agent.services.voice_deletion_ledger import VoiceDeletionLedger, VoiceDeletionLedgerRecord
from agent.services.voice_governance_domain import (
    VoicePrincipal,
    voice_idempotency_key_digest,
    voice_scope_digest,
)


@dataclass(frozen=True)
class VoiceDeletionClaim:
    scope_digest: str
    deleted_at: float
    replayed: bool


class VoiceDeletionTombstoneRepository:
    """Database projection of the external pseudonymous deletion ledger."""

    def __init__(self, ledger: VoiceDeletionLedger | None = None) -> None:
        self._ledger = ledger or VoiceDeletionLedger()

    def claim(
        self,
        principal: VoicePrincipal,
        profile_id: str,
        *,
        idempotency_key: str,
    ) -> VoiceDeletionClaim:
        scope_digest = voice_scope_digest(principal, profile_id)
        ledger_claim = self._ledger.claim(
            scope_digest=scope_digest,
            idempotency_key_digest=voice_idempotency_key_digest(
                idempotency_key,
                scope_digest=scope_digest,
                operation="profile_delete",
            ),
        )
        self._project(ledger_claim.record)
        return VoiceDeletionClaim(
            scope_digest=scope_digest,
            deleted_at=ledger_claim.record.deleted_at,
            replayed=ledger_claim.replayed,
        )

    def sync_from_ledger(self) -> int:
        records = self._ledger.read_all()
        for record in records:
            self._project(record)
        return len(records)

    def list_page(
        self,
        *,
        after: tuple[float, str] | None = None,
        limit: int = 500,
    ) -> tuple[VoiceDeletionTombstoneDB, ...]:
        bounded_limit = max(1, min(int(limit), 10_000))
        with Session(engine) as session:
            statement = select(VoiceDeletionTombstoneDB)
            if after is not None:
                deleted_at, tombstone_id = after
                statement = statement.where(
                    (VoiceDeletionTombstoneDB.deleted_at > deleted_at)
                    | (
                        (VoiceDeletionTombstoneDB.deleted_at == deleted_at)
                        & (VoiceDeletionTombstoneDB.id > tombstone_id)
                    )
                )
            rows = session.exec(
                statement
                .order_by(VoiceDeletionTombstoneDB.deleted_at, VoiceDeletionTombstoneDB.id)
                .limit(bounded_limit)
            ).all()
            return tuple(rows)

    def mark_reconciled(self, scope_digest: str) -> bool:
        with Session(engine) as session:
            tombstone = self._find(session, scope_digest)
            if tombstone is None:
                return False
            tombstone.reconciliation_count += 1
            tombstone.last_reconciled_at = time.time()
            session.add(tombstone)
            session.commit()
            return True

    def _project(self, record: VoiceDeletionLedgerRecord) -> VoiceDeletionTombstoneDB:
        for attempt in range(2):
            with Session(engine) as session:
                tombstone = self._find(session, record.scope_digest)
                if tombstone is None:
                    tombstone = VoiceDeletionTombstoneDB(
                        scope_digest=record.scope_digest,
                        deleted_at=record.deleted_at,
                        idempotency_key_digests=[record.idempotency_key_digest],
                    )
                else:
                    tombstone.deleted_at = max(tombstone.deleted_at, record.deleted_at)
                    tombstone.idempotency_key_digests = sorted(
                        {*tombstone.idempotency_key_digests, record.idempotency_key_digest}
                    )
                session.add(tombstone)
                try:
                    session.commit()
                    session.refresh(tombstone)
                    return tombstone
                except IntegrityError:
                    session.rollback()
                    if attempt:
                        raise
        raise RuntimeError("voice deletion tombstone projection failed")

    @staticmethod
    def _find(session: Session, scope_digest: str) -> VoiceDeletionTombstoneDB | None:
        return session.exec(
            select(VoiceDeletionTombstoneDB).where(
                VoiceDeletionTombstoneDB.scope_digest == scope_digest,
            )
        ).first()
