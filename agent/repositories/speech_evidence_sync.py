"""Durable, multi-Hub-safe adapters for speech-evidence sync control state."""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace

from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.speech_evidence_sync import SpeechEvidenceOfferDB
from agent.models.semantic_media_audit import SemanticMediaAuditEvent
from agent.models.speech_evidence_offer import SpeechEvidenceOfferError, SpeechEvidenceOfferRecord
from agent.repositories.semantic_media_audit_outbox import SqlSemanticMediaAuditOutbox
from agent.repositories.speech_evidence_offer_rows import offer_record as _offer_record
from agent.repositories.speech_evidence_offer_rows import offer_row as _offer_row
from agent.repositories.speech_evidence_offer_rows import offer_values as _offer_values
from agent.repositories.speech_evidence_peer_keys import SqlSpeechEvidencePeerKeyRegistry
from agent.repositories.speech_evidence_replay_window import SqlSpeechEvidenceReplayWindow
from agent.repositories.speech_evidence_sync_records import (
    SpeechEvidencePeerKeyRecord,
    SpeechEvidenceSyncRepositoryError,
    SpeechEvidenceTransferChunkBinding,
    SpeechEvidenceTransferCurationBinding,
    SpeechEvidenceTransferRecord,
)
from agent.repositories.speech_evidence_transfers import SqlSpeechEvidenceTransferRepository

_OFFER_LOCK_STRIPES = tuple(threading.RLock() for _ in range(64))


class SqlSpeechEvidenceOfferRepository:
    def __init__(self, *, clock_ms=lambda: time.time_ns() // 1_000_000) -> None:
        self._clock_ms = clock_ms

    def get(self, offer_id: str) -> SpeechEvidenceOfferRecord | None:
        with Session(engine) as session:
            row = session.get(SpeechEvidenceOfferDB, offer_id)
            return _offer_record(row) if row is not None else None

    @contextmanager
    def curation_guard(
        self,
        *,
        tenant_id: str,
        offer: SpeechEvidenceOfferRecord,
    ) -> Iterator[None]:
        """Serialize curation with invalidation on the canonical Offer row.

        PostgreSQL-compatible engines use ``FOR UPDATE`` across the bounded
        curation transaction.  The striped process lock gives SQLite's
        development/test adapter equivalent in-process semantics without
        taking a database-wide write lock that would block evidence writes.
        """

        with _offer_process_lock(offer.offer_id), Session(engine) as session:
            row = session.exec(
                select(SpeechEvidenceOfferDB)
                .where(
                    SpeechEvidenceOfferDB.offer_id == offer.offer_id,
                    SpeechEvidenceOfferDB.tenant_id == tenant_id,
                )
                .with_for_update()
            ).first()
            if (
                row is None
                or row.state != "accepted"
                or not row.transfer_started
                or row.expires_at_ms <= int(self._clock_ms())
                or _offer_record(row) != offer
            ):
                raise SpeechEvidenceSyncRepositoryError(
                    "speech_evidence_curation_offer_stale",
                    status_code=409,
                )
            yield
            session.commit()

    def list_for_participant(
        self,
        *,
        tenant_id: str,
        session_id: str,
        pair_id: str,
        participant_id: str,
        epoch: int,
        limit: int = 50,
    ) -> tuple[SpeechEvidenceOfferRecord, ...]:
        """Return only content-free offer metadata visible to one current pair member."""

        with Session(engine) as session:
            rows = session.exec(
                select(SpeechEvidenceOfferDB)
                .where(
                    SpeechEvidenceOfferDB.tenant_id == tenant_id,
                    SpeechEvidenceOfferDB.session_id == session_id,
                    SpeechEvidenceOfferDB.pair_id == pair_id,
                    SpeechEvidenceOfferDB.epoch == epoch,
                    or_(
                        SpeechEvidenceOfferDB.sender_id == participant_id,
                        SpeechEvidenceOfferDB.recipient_id == participant_id,
                    ),
                )
                .order_by(
                    SpeechEvidenceOfferDB.updated_at_ms.desc(),
                    SpeechEvidenceOfferDB.offer_id.asc(),
                )
                .limit(max(1, min(int(limit), 50)))
            ).all()
        return tuple(_offer_record(row) for row in rows)

    def put_if_absent(
        self,
        record: SpeechEvidenceOfferRecord,
        *,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> SpeechEvidenceOfferRecord:
        row = _offer_row(record, now_ms=int(self._clock_ms()))
        try:
            with Session(engine) as session:
                current = session.get(SpeechEvidenceOfferDB, record.offer_id)
                if current is not None:
                    if audit_event is not None and _offer_record(current) == record:
                        SqlSemanticMediaAuditOutbox.enqueue_in_session(session, audit_event)
                        session.commit()
                    return _offer_record(current)
                session.add(row)
                if audit_event is not None:
                    SqlSemanticMediaAuditOutbox.enqueue_in_session(session, audit_event)
                session.commit()
                session.refresh(row)
                return _offer_record(row)
        except IntegrityError:
            current = self.get(record.offer_id)
            if current is None:
                raise SpeechEvidenceOfferError("speech_evidence_offer_write_conflict", status_code=409)
            if audit_event is not None and current == record:
                with Session(engine) as session:
                    SqlSemanticMediaAuditOutbox.enqueue_in_session(session, audit_event)
                    session.commit()
            return current

    def compare_and_set(
        self,
        offer_id: str,
        *,
        expected_state: str,
        record: SpeechEvidenceOfferRecord,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> SpeechEvidenceOfferRecord:
        with _offer_process_lock(offer_id):
            return self._compare_and_set(
                offer_id,
                expected_state=expected_state,
                record=record,
                audit_event=audit_event,
            )

    def _compare_and_set(
        self,
        offer_id: str,
        *,
        expected_state: str,
        record: SpeechEvidenceOfferRecord,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> SpeechEvidenceOfferRecord:
        now = int(self._clock_ms())
        values = _offer_values(replace(record, version=record.version + 1))
        values["updated_at_ms"] = now
        with Session(engine) as session:
            result = session.exec(
                update(SpeechEvidenceOfferDB)
                .where(
                    SpeechEvidenceOfferDB.offer_id == offer_id,
                    SpeechEvidenceOfferDB.state == expected_state,
                    SpeechEvidenceOfferDB.version == record.version,
                )
                .values(**values)
            )
            if result.rowcount != 1:
                session.rollback()
                current = session.get(SpeechEvidenceOfferDB, offer_id)
                if current is None:
                    raise SpeechEvidenceOfferError("speech_evidence_offer_not_found", status_code=404)
                candidate = replace(record, version=record.version + 1)
                if _offer_record(current) == candidate:
                    if audit_event is not None:
                        SqlSemanticMediaAuditOutbox.enqueue_in_session(session, audit_event)
                        session.commit()
                    return candidate
                raise SpeechEvidenceOfferError("speech_evidence_offer_state_conflict", status_code=409)
            if audit_event is not None:
                SqlSemanticMediaAuditOutbox.enqueue_in_session(session, audit_event)
            session.commit()
            current = session.get(SpeechEvidenceOfferDB, offer_id)
            if current is None:
                raise SpeechEvidenceOfferError("speech_evidence_offer_not_found", status_code=404)
            return _offer_record(current)

    def invalidate_scope(
        self,
        *,
        tenant_id: str,
        session_id: str,
        reason_code: str,
        before_epoch: int | None = None,
    ) -> int:
        statement = (
            update(SpeechEvidenceOfferDB)
            .where(
                SpeechEvidenceOfferDB.tenant_id == tenant_id,
                SpeechEvidenceOfferDB.session_id == session_id,
                SpeechEvidenceOfferDB.state.in_(["proposed", "accepted"]),
            )
            .values(
                state="invalidated",
                invalidation_reason=reason_code,
                version=SpeechEvidenceOfferDB.version + 1,
                updated_at_ms=int(self._clock_ms()),
            )
        )
        if before_epoch is not None:
            statement = statement.where(SpeechEvidenceOfferDB.epoch < before_epoch)
        with Session(engine) as session:
            result = session.exec(statement)
            session.commit()
            return int(result.rowcount)


@contextmanager
def _offer_process_lock(offer_id: str) -> Iterator[None]:
    digest_value = hashlib.sha256(offer_id.encode("utf-8")).digest()
    lock = _OFFER_LOCK_STRIPES[int.from_bytes(digest_value[:2], "big") % len(_OFFER_LOCK_STRIPES)]
    with lock:
        yield


__all__ = [
    "SpeechEvidenceTransferChunkBinding",
    "SpeechEvidenceTransferCurationBinding",
    "SpeechEvidencePeerKeyRecord",
    "SpeechEvidenceSyncRepositoryError",
    "SpeechEvidenceTransferRecord",
    "SqlSpeechEvidenceOfferRepository",
    "SqlSpeechEvidencePeerKeyRegistry",
    "SqlSpeechEvidenceReplayWindow",
    "SqlSpeechEvidenceTransferRepository",
]
