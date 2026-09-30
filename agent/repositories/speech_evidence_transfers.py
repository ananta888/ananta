"""Bounded, resumable speech-evidence chunk transfers fenced by the accepted Offer."""

from __future__ import annotations

import base64
import hashlib
import time

from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.speech_evidence_sync import (
    SpeechEvidenceOfferDB,
    SpeechEvidenceTransferChunkDB,
    SpeechEvidenceTransferDB,
)
from agent.models.speech_evidence_offer import (
    SpeechEvidenceGroupPreview,
    SpeechEvidenceOfferRecord,
    group_preview_digest,
)
from agent.repositories.speech_evidence_offer_rows import offer_record as _offer_record
from agent.repositories.speech_evidence_sync_records import (
    SpeechEvidenceSyncRepositoryError,
    SpeechEvidenceTransferChunkBinding,
    SpeechEvidenceTransferCurationBinding,
    SpeechEvidenceTransferRecord,
)
from ananta_contracts.speech_evidence_sync import (
    GROUP_PREVIEW_VERSION,
    OFFER_PROTOCOL_VERSION,
    VerifiedSpeechEvidenceMessage,
    group_preview_group_id,
    group_preview_resolution_digest,
)


class SqlSpeechEvidenceTransferRepository:
    MAX_IN_FLIGHT_BYTES = 1024 * 1024

    def __init__(self, *, clock_ms=lambda: time.time_ns() // 1_000_000) -> None:
        self._clock_ms = clock_ms

    def register_chunk(
        self,
        *,
        tenant_id: str,
        offer: SpeechEvidenceOfferRecord,
        message: VerifiedSpeechEvidenceMessage,
    ) -> SpeechEvidenceTransferRecord:
        payload = message.payload
        if message.header.message_type != "chunk":
            raise SpeechEvidenceSyncRepositoryError("speech_evidence_chunk_required", status_code=422)
        group_id = str(payload["group_id"])
        preview = _required_group_preview(offer, group_id)
        transfer_sender, transfer_recipient = _transfer_participants(offer)
        if (
            offer.tenant_id != tenant_id
            or payload.get("offer_id") != offer.offer_id
            or group_id not in offer.group_ids
            or message.header.session_id != offer.session_id
            or message.header.pair_id != offer.pair_id
            or message.header.sender_id != transfer_sender
            or message.header.audience_id != transfer_recipient
            or message.header.epoch != offer.epoch
        ):
            raise SpeechEvidenceSyncRepositoryError("speech_evidence_transfer_binding_mismatch", status_code=403)
        now = int(self._clock_ms())
        if now >= min(offer.expires_at_ms, message.header.expires_at_ms):
            raise SpeechEvidenceSyncRepositoryError("speech_evidence_offer_expired", status_code=410)
        with Session(engine) as session:
            _lock_current_offer(session, tenant_id=tenant_id, offer=offer, now_ms=now)
            transfer = session.exec(
                select(SpeechEvidenceTransferDB)
                .where(
                    SpeechEvidenceTransferDB.offer_id == offer.offer_id,
                    SpeechEvidenceTransferDB.group_id == group_id,
                )
                .with_for_update()
            ).first()
            count = int(payload["chunk_count"])
            if transfer is None:
                transfer = SpeechEvidenceTransferDB(
                    tenant_id=tenant_id,
                    offer_id=offer.offer_id,
                    group_id=group_id,
                    session_id=offer.session_id,
                    pair_id=offer.pair_id,
                    epoch=offer.epoch,
                    sender_id=transfer_sender,
                    recipient_id=transfer_recipient,
                    key_id=message.header.key_id,
                    chunk_count=count,
                    expires_at_ms=min(offer.expires_at_ms, message.header.expires_at_ms),
                    created_at_ms=now,
                    updated_at_ms=now,
                )
                session.add(transfer)
                session.flush()
            elif (
                transfer.state != "active"
                or transfer.chunk_count != count
                or transfer.key_id != message.header.key_id
                or transfer.epoch != message.header.epoch
            ):
                raise SpeechEvidenceSyncRepositoryError("speech_evidence_transfer_state_conflict")
            index = int(payload["chunk_index"])
            nonce = base64.b64decode(str(payload["nonce_b64"]), validate=True)
            nonce_digest = hashlib.sha256(nonce).hexdigest()
            nonce_scope_digest = hashlib.sha256(
                "\0".join(
                    (
                        tenant_id,
                        offer.session_id,
                        offer.pair_id,
                        transfer_sender,
                        transfer_recipient,
                        message.header.key_id,
                        str(message.header.epoch),
                        offer.direction,
                        nonce_digest,
                    )
                ).encode("utf-8")
            ).hexdigest()
            existing = session.exec(
                select(SpeechEvidenceTransferChunkDB).where(
                    SpeechEvidenceTransferChunkDB.transfer_id == transfer.id,
                    SpeechEvidenceTransferChunkDB.chunk_index == index,
                )
            ).first()
            if existing is not None:
                if (
                    existing.plaintext_digest == payload["plaintext_digest"]
                    and existing.ciphertext_digest == payload["ciphertext_digest"]
                    and existing.plaintext_bytes == int(payload["plaintext_bytes"])
                    and existing.nonce_scope_digest == nonce_scope_digest
                ):
                    # A resume is signed with a fresh monotone sequence/message
                    # identifier.  The encrypted chunk itself is immutable, so
                    # an exact digest match is an idempotent delivery rather
                    # than an index conflict.  Changed content at the same
                    # index remains fail-closed below.
                    return _transfer_record(transfer)
                raise SpeechEvidenceSyncRepositoryError("speech_evidence_chunk_index_conflict")
            reused = session.exec(
                select(SpeechEvidenceTransferChunkDB.id).where(
                    SpeechEvidenceTransferChunkDB.nonce_scope_digest == nonce_scope_digest,
                )
            ).first()
            if reused is not None:
                raise SpeechEvidenceSyncRepositoryError("speech_evidence_nonce_reused")
            plain_bytes = int(payload["plaintext_bytes"])
            group_total = int(
                session.exec(
                    select(func.coalesce(func.sum(SpeechEvidenceTransferChunkDB.plaintext_bytes), 0)).where(
                        SpeechEvidenceTransferChunkDB.transfer_id == transfer.id
                    )
                ).one()
            )
            if group_total + plain_bytes > preview.size_bytes:
                raise SpeechEvidenceSyncRepositoryError(
                    "speech_evidence_offer_preview_size_exceeded",
                    status_code=413,
                )
            total = int(
                session.exec(
                    select(func.coalesce(func.sum(SpeechEvidenceTransferChunkDB.plaintext_bytes), 0))
                    .join(
                        SpeechEvidenceTransferDB,
                        SpeechEvidenceTransferDB.id == SpeechEvidenceTransferChunkDB.transfer_id,
                    )
                    .where(SpeechEvidenceTransferDB.offer_id == offer.offer_id)
                ).one()
            )
            if total + plain_bytes > offer.total_bytes:
                raise SpeechEvidenceSyncRepositoryError("speech_evidence_offer_byte_budget_exceeded", status_code=413)
            in_flight = int(
                session.exec(
                    select(func.coalesce(func.sum(SpeechEvidenceTransferDB.in_flight_bytes), 0)).where(
                        SpeechEvidenceTransferDB.tenant_id == tenant_id,
                        SpeechEvidenceTransferDB.offer_id == offer.offer_id,
                        SpeechEvidenceTransferDB.state == "active",
                    )
                ).one()
            )
            if in_flight + plain_bytes > self.MAX_IN_FLIGHT_BYTES:
                raise SpeechEvidenceSyncRepositoryError("speech_evidence_transfer_backpressure", status_code=429)
            session.add(
                SpeechEvidenceTransferChunkDB(
                    transfer_id=transfer.id,
                    message_id=message.header.message_id,
                    chunk_index=index,
                    plaintext_bytes=plain_bytes,
                    plaintext_digest=str(payload["plaintext_digest"]),
                    ciphertext_digest=str(payload["ciphertext_digest"]),
                    nonce_digest=nonce_digest,
                    nonce_scope_digest=nonce_scope_digest,
                    key_id=message.header.key_id,
                    epoch=message.header.epoch,
                    direction=offer.direction,
                    created_at_ms=now,
                )
            )
            transfer.in_flight_bytes += plain_bytes
            transfer.version += 1
            transfer.updated_at_ms = now
            session.add(transfer)
            try:
                session.commit()
            except IntegrityError as exc:
                raise SpeechEvidenceSyncRepositoryError("speech_evidence_chunk_write_conflict") from exc
            session.refresh(transfer)
            return _transfer_record(transfer)

    def acknowledge(
        self,
        *,
        tenant_id: str,
        offer: SpeechEvidenceOfferRecord,
        message: VerifiedSpeechEvidenceMessage,
    ) -> SpeechEvidenceTransferRecord:
        payload = message.payload
        if message.header.message_type != "chunk_ack":
            raise SpeechEvidenceSyncRepositoryError("speech_evidence_ack_required", status_code=422)
        group_id = str(payload["group_id"])
        preview = _required_group_preview(offer, group_id)
        transfer_sender, transfer_recipient = _transfer_participants(offer)
        if (
            offer.tenant_id != tenant_id
            or payload.get("offer_id") != offer.offer_id
            or message.header.sender_id != transfer_recipient
            or message.header.audience_id != transfer_sender
            or message.header.session_id != offer.session_id
            or message.header.pair_id != offer.pair_id
            or message.header.epoch != offer.epoch
        ):
            raise SpeechEvidenceSyncRepositoryError("speech_evidence_ack_binding_mismatch", status_code=403)
        now = int(self._clock_ms())
        with Session(engine) as session:
            _lock_current_offer(session, tenant_id=tenant_id, offer=offer, now_ms=now)
            transfer = session.exec(
                select(SpeechEvidenceTransferDB)
                .where(
                    SpeechEvidenceTransferDB.tenant_id == tenant_id,
                    SpeechEvidenceTransferDB.offer_id == offer.offer_id,
                    SpeechEvidenceTransferDB.group_id == group_id,
                )
                .with_for_update()
            ).first()
            if transfer is None:
                raise SpeechEvidenceSyncRepositoryError("speech_evidence_transfer_not_found", status_code=404)
            if transfer.state not in {"active", "completed"} or transfer.expires_at_ms <= now:
                raise SpeechEvidenceSyncRepositoryError("speech_evidence_transfer_inactive", status_code=410)
            requested = {int(value) for value in payload["acknowledged_indices"]}
            chunks = session.exec(
                select(SpeechEvidenceTransferChunkDB).where(SpeechEvidenceTransferChunkDB.transfer_id == transfer.id)
            ).all()
            by_index = {int(item.chunk_index): item for item in chunks}
            if requested - set(by_index):
                raise SpeechEvidenceSyncRepositoryError("speech_evidence_ack_unknown_chunk")
            acknowledged = set(int(value) for value in transfer.acknowledged_indices)
            acknowledged.update(requested)
            first_missing = 0
            while first_missing in acknowledged:
                first_missing += 1
            received = sum(by_index[index].plaintext_bytes for index in acknowledged)
            if (
                int(payload["first_missing_index"]) != first_missing
                or int(payload["received_bytes"]) != received
                or first_missing < transfer.first_missing_index
                or (payload["complete"] is True) != (first_missing == transfer.chunk_count)
            ):
                raise SpeechEvidenceSyncRepositoryError("speech_evidence_ack_cursor_invalid")
            if first_missing == transfer.chunk_count and received != preview.size_bytes:
                raise SpeechEvidenceSyncRepositoryError("speech_evidence_offer_preview_size_mismatch")
            newly_acked = requested - set(int(value) for value in transfer.acknowledged_indices)
            for index in newly_acked:
                by_index[index].acknowledged = True
                session.add(by_index[index])
            transfer.acknowledged_indices = sorted(acknowledged)
            transfer.first_missing_index = first_missing
            transfer.received_bytes = received
            transfer.in_flight_bytes = max(
                0,
                transfer.in_flight_bytes - sum(by_index[index].plaintext_bytes for index in newly_acked),
            )
            if first_missing == transfer.chunk_count:
                transfer.state = "completed"
            transfer.version += 1
            transfer.updated_at_ms = now
            session.add(transfer)
            session.commit()
            session.refresh(transfer)
            return _transfer_record(transfer)

    def get(
        self,
        *,
        tenant_id: str,
        offer_id: str,
        group_id: str,
    ) -> SpeechEvidenceTransferRecord | None:
        with Session(engine) as session:
            row = session.exec(
                select(SpeechEvidenceTransferDB).where(
                    SpeechEvidenceTransferDB.tenant_id == tenant_id,
                    SpeechEvidenceTransferDB.offer_id == offer_id,
                    SpeechEvidenceTransferDB.group_id == group_id,
                )
            ).first()
            return _transfer_record(row) if row is not None else None

    def curation_binding(
        self,
        *,
        tenant_id: str,
        offer_id: str,
        group_id: str,
    ) -> SpeechEvidenceTransferCurationBinding | None:
        """Return the exact acknowledged plaintext commitment for Hub curation.

        This projection never exposes relay ciphertext, nonces or keys.  It is
        usable only after the recipient has acknowledged every signed chunk.
        """

        now = int(self._clock_ms())
        with Session(engine) as session:
            transfer = session.exec(
                select(SpeechEvidenceTransferDB).where(
                    SpeechEvidenceTransferDB.tenant_id == tenant_id,
                    SpeechEvidenceTransferDB.offer_id == offer_id,
                    SpeechEvidenceTransferDB.group_id == group_id,
                )
            ).first()
            offer = session.get(SpeechEvidenceOfferDB, offer_id)
            if (
                transfer is None
                or offer is None
                or transfer.state != "completed"
                or transfer.expires_at_ms <= now
                or transfer.first_missing_index != transfer.chunk_count
            ):
                return None
            try:
                previews = tuple(
                    SpeechEvidenceGroupPreview.from_mapping(value)
                    for value in (offer.group_previews or [])
                    if isinstance(value, dict)
                )
                preview = next(value for value in previews if value.group_id == group_id)
            except (KeyError, StopIteration, TypeError, ValueError):
                return None
            if (
                offer.protocol_version != OFFER_PROTOCOL_VERSION
                or offer.state != "accepted"
                or not offer.transfer_started
                or offer.expires_at_ms <= now
                or len(previews) != len(offer.group_previews or [])
                or set(offer.group_ids or []) != {value.group_id for value in previews}
                or sum(value.size_bytes for value in previews) != offer.total_bytes
                or group_preview_digest(previews) != offer.group_preview_digest
                or any(
                    value.preview_version != GROUP_PREVIEW_VERSION
                    or value.group_id
                    != group_preview_group_id(value.source_group_digest, value.revision)
                    or value.resolution_digest
                    != group_preview_resolution_digest(value.source_group_digest, value.revision)
                    for value in previews
                )
                or int(transfer.received_bytes) != preview.size_bytes
            ):
                return None
            chunks = session.exec(
                select(SpeechEvidenceTransferChunkDB)
                .where(
                    SpeechEvidenceTransferChunkDB.transfer_id == transfer.id,
                    SpeechEvidenceTransferChunkDB.acknowledged.is_(True),
                )
                .order_by(SpeechEvidenceTransferChunkDB.chunk_index.asc())
            ).all()
            if (
                len(chunks) != transfer.chunk_count
                or [int(row.chunk_index) for row in chunks] != list(range(transfer.chunk_count))
                or sum(int(row.plaintext_bytes) for row in chunks) != transfer.received_bytes
            ):
                return None
            return SpeechEvidenceTransferCurationBinding(
                offer_id=transfer.offer_id,
                group_id=transfer.group_id,
                session_id=transfer.session_id,
                pair_id=transfer.pair_id,
                epoch=transfer.epoch,
                sender_id=transfer.sender_id,
                recipient_id=transfer.recipient_id,
                key_id=transfer.key_id,
                received_bytes=transfer.received_bytes,
                expires_at_ms=transfer.expires_at_ms,
                preview=preview,
                offer_group_preview_digest=offer.group_preview_digest,
                chunks=tuple(
                    SpeechEvidenceTransferChunkBinding(
                        chunk_index=int(row.chunk_index),
                        plaintext_bytes=int(row.plaintext_bytes),
                        plaintext_digest=str(row.plaintext_digest),
                    )
                    for row in chunks
                ),
            )

    def invalidate_offer(self, *, tenant_id: str, offer_id: str, reason_code: str) -> int:
        with Session(engine) as session:
            result = session.exec(
                update(SpeechEvidenceTransferDB)
                .where(
                    SpeechEvidenceTransferDB.tenant_id == tenant_id,
                    SpeechEvidenceTransferDB.offer_id == offer_id,
                    SpeechEvidenceTransferDB.state == "active",
                )
                .values(
                    state="invalidated",
                    reason_code=reason_code,
                    in_flight_bytes=0,
                    version=SpeechEvidenceTransferDB.version + 1,
                    updated_at_ms=int(self._clock_ms()),
                )
            )
            session.commit()
            return int(result.rowcount)

    def message_ids(self, *, tenant_id: str, offer_id: str) -> tuple[str, ...]:
        """Return opaque-relay identifiers without exposing evidence content."""

        with Session(engine) as session:
            rows = session.exec(
                select(SpeechEvidenceTransferChunkDB.message_id)
                .join(
                    SpeechEvidenceTransferDB,
                    SpeechEvidenceTransferDB.id == SpeechEvidenceTransferChunkDB.transfer_id,
                )
                .where(
                    SpeechEvidenceTransferDB.tenant_id == tenant_id,
                    SpeechEvidenceTransferDB.offer_id == offer_id,
                )
            ).all()
            return tuple(sorted(str(value) for value in rows))


def _transfer_participants(offer: SpeechEvidenceOfferRecord) -> tuple[str, str]:
    if offer.direction == "sender_to_receiver":
        return offer.sender_id, offer.recipient_id
    if offer.direction == "receiver_to_sender":
        return offer.recipient_id, offer.sender_id
    raise SpeechEvidenceSyncRepositoryError("speech_evidence_direction_invalid", status_code=422)


def _required_group_preview(
    offer: SpeechEvidenceOfferRecord,
    group_id: str,
) -> SpeechEvidenceGroupPreview:
    preview = next((value for value in offer.group_previews if value.group_id == group_id), None)
    if preview is None:
        raise SpeechEvidenceSyncRepositoryError(
            "speech_evidence_offer_preview_required",
            status_code=409,
        )
    return preview


def _lock_current_offer(
    session: Session,
    *,
    tenant_id: str,
    offer: SpeechEvidenceOfferRecord,
    now_ms: int,
) -> SpeechEvidenceOfferDB:
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
        or row.expires_at_ms <= now_ms
        or _offer_record(row) != offer
    ):
        raise SpeechEvidenceSyncRepositoryError("speech_evidence_offer_state_conflict", status_code=409)
    return row


def _transfer_record(row: SpeechEvidenceTransferDB) -> SpeechEvidenceTransferRecord:
    return SpeechEvidenceTransferRecord(
        offer_id=row.offer_id,
        group_id=row.group_id,
        state=row.state,
        chunk_count=int(row.chunk_count),
        acknowledged_chunks=len(row.acknowledged_indices),
        first_missing_index=int(row.first_missing_index),
        received_bytes=int(row.received_bytes),
        in_flight_bytes=int(row.in_flight_bytes),
        expires_at_ms=int(row.expires_at_ms),
        reason_code=row.reason_code,
        version=int(row.version),
    )


__all__ = ["SqlSpeechEvidenceTransferRepository"]
