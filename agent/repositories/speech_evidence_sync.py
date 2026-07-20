"""Durable, multi-Hub-safe adapters for speech-evidence sync control state."""

from __future__ import annotations

import base64
import hashlib
import re
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import delete, func, or_, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.speech_evidence_sync import (
    SpeechEvidenceOfferDB,
    SpeechEvidencePeerKeyDB,
    SpeechEvidenceReplayStateDB,
    SpeechEvidenceTransferChunkDB,
    SpeechEvidenceTransferDB,
)
from agent.repositories.semantic_media_audit_outbox import SqlSemanticMediaAuditOutbox
from agent.services.semantic_media_audit_service import SemanticMediaAuditEvent
from agent.services.speech_evidence_offer_service import (
    SpeechEvidenceGroupPreview,
    SpeechEvidenceOfferError,
    SpeechEvidenceOfferRecord,
    group_preview_digest,
)
from ananta_contracts.speech_evidence_sync import (
    GROUP_PREVIEW_VERSION,
    MAX_SEQUENCE,
    OFFER_PROTOCOL_VERSION,
    SpeechEvidenceProtocolError,
    VerifiedSpeechEvidenceMessage,
    group_preview_group_id,
    group_preview_resolution_digest,
)

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_OFFER_LOCK_STRIPES = tuple(threading.RLock() for _ in range(64))


class SpeechEvidenceSyncRepositoryError(RuntimeError):
    def __init__(self, reason_code: str, *, status_code: int = 409) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class SpeechEvidencePeerKeyRecord:
    tenant_id: str
    session_id: str
    pair_id: str
    sender_id: str
    audience_id: str
    epoch: int
    key_id: str
    public_key_b64: str
    fingerprint: str
    membership_version: int
    consent_version: int
    expires_at_ms: int
    state: str
    version: int

    def public_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SpeechEvidenceTransferChunkBinding:
    chunk_index: int
    plaintext_bytes: int
    plaintext_digest: str


@dataclass(frozen=True, slots=True)
class SpeechEvidenceTransferCurationBinding:
    offer_id: str
    group_id: str
    session_id: str
    pair_id: str
    epoch: int
    sender_id: str
    recipient_id: str
    key_id: str
    received_bytes: int
    expires_at_ms: int
    preview: SpeechEvidenceGroupPreview
    offer_group_preview_digest: str
    chunks: tuple[SpeechEvidenceTransferChunkBinding, ...]


class SqlSpeechEvidencePeerKeyRegistry:
    """Immutable current-epoch verification keys; conflicting replacement is forbidden."""

    MAX_TTL_MS = 10 * 60 * 1000

    def __init__(self, *, clock_ms=lambda: time.time_ns() // 1_000_000) -> None:
        self._clock_ms = clock_ms

    def register(
        self,
        *,
        tenant_id: str,
        session_id: str,
        pair_id: str,
        sender_id: str,
        audience_id: str,
        epoch: int,
        key_id: str,
        public_key_b64: str,
        membership_version: int,
        consent_version: int,
        expires_at_ms: int,
    ) -> tuple[SpeechEvidencePeerKeyRecord, bool]:
        now = int(self._clock_ms())
        for value in (tenant_id, session_id, pair_id, sender_id, audience_id, key_id):
            if _IDENTIFIER.fullmatch(value) is None:
                raise SpeechEvidenceSyncRepositoryError("speech_evidence_key_binding_invalid", status_code=422)
        if sender_id == audience_id or type(epoch) is not int or epoch < 1:
            raise SpeechEvidenceSyncRepositoryError("speech_evidence_key_binding_invalid", status_code=422)
        if type(membership_version) is not int or membership_version < 1:
            raise SpeechEvidenceSyncRepositoryError("speech_evidence_membership_stale", status_code=409)
        if type(consent_version) is not int or consent_version < 1:
            raise SpeechEvidenceSyncRepositoryError("speech_evidence_consent_stale", status_code=409)
        if type(expires_at_ms) is not int or not now < expires_at_ms <= now + self.MAX_TTL_MS:
            raise SpeechEvidenceSyncRepositoryError("speech_evidence_key_expiry_invalid", status_code=422)
        raw = _public_key_bytes(public_key_b64)
        fingerprint = hashlib.sha256(raw).hexdigest()
        row = SpeechEvidencePeerKeyDB(
            tenant_id=tenant_id,
            session_id=session_id,
            pair_id=pair_id,
            sender_id=sender_id,
            audience_id=audience_id,
            epoch=epoch,
            key_id=key_id,
            public_key_b64=base64.b64encode(raw).decode("ascii"),
            fingerprint=fingerprint,
            membership_version=membership_version,
            consent_version=consent_version,
            expires_at_ms=expires_at_ms,
            created_at_ms=now,
            updated_at_ms=now,
        )
        try:
            with Session(engine) as session:
                existing = _key_row(
                    session,
                    lock=True,
                    tenant_id=tenant_id,
                    session_id=session_id,
                    pair_id=pair_id,
                    sender_id=sender_id,
                    audience_id=audience_id,
                    epoch=epoch,
                    key_id=key_id,
                )
                if existing is not None:
                    if (
                        existing.fingerprint != fingerprint
                        or existing.public_key_b64 != row.public_key_b64
                        or existing.membership_version != membership_version
                    ):
                        raise SpeechEvidenceSyncRepositoryError("speech_evidence_key_substitution", status_code=409)
                    if existing.state != "active" or existing.expires_at_ms <= now:
                        raise SpeechEvidenceSyncRepositoryError("speech_evidence_key_inactive", status_code=410)
                    if consent_version < existing.consent_version:
                        raise SpeechEvidenceSyncRepositoryError("speech_evidence_consent_stale", status_code=409)
                    if consent_version > existing.consent_version or expires_at_ms > existing.expires_at_ms:
                        existing.consent_version = consent_version
                        existing.expires_at_ms = max(existing.expires_at_ms, expires_at_ms)
                        existing.version += 1
                        existing.updated_at_ms = now
                        session.add(existing)
                        session.commit()
                        session.refresh(existing)
                    return _key_record(existing), False
                session.add(row)
                session.commit()
                session.refresh(row)
                return _key_record(row), True
        except IntegrityError as exc:
            existing = self.get(
                tenant_id=tenant_id,
                session_id=session_id,
                pair_id=pair_id,
                sender_id=sender_id,
                audience_id=audience_id,
                epoch=epoch,
                key_id=key_id,
            )
            if (
                existing is not None
                and existing.fingerprint == fingerprint
                and existing.membership_version == membership_version
            ):
                if existing.consent_version < consent_version or existing.expires_at_ms < expires_at_ms:
                    return self.register(
                        tenant_id=tenant_id,
                        session_id=session_id,
                        pair_id=pair_id,
                        sender_id=sender_id,
                        audience_id=audience_id,
                        epoch=epoch,
                        key_id=key_id,
                        public_key_b64=public_key_b64,
                        membership_version=membership_version,
                        consent_version=consent_version,
                        expires_at_ms=expires_at_ms,
                    )
                return existing, False
            raise SpeechEvidenceSyncRepositoryError("speech_evidence_key_write_conflict") from exc

    def get(self, **scope: object) -> SpeechEvidencePeerKeyRecord | None:
        now = int(self._clock_ms())
        with Session(engine) as session:
            row = _key_row(session, **scope)
            if row is None or row.state != "active" or row.expires_at_ms <= now:
                return None
            return _key_record(row)

    def resolve(self, **scope: object) -> Ed25519PublicKey | None:
        row = self.get(**scope)
        if row is None:
            return None
        try:
            return Ed25519PublicKey.from_public_bytes(base64.b64decode(row.public_key_b64, validate=True))
        except ValueError:
            return None

    def invalidate_scope(
        self,
        *,
        tenant_id: str,
        session_id: str,
        sender_id: str | None = None,
        before_epoch: int | None = None,
    ) -> int:
        statement = (
            update(SpeechEvidencePeerKeyDB)
            .where(
                SpeechEvidencePeerKeyDB.tenant_id == tenant_id,
                SpeechEvidencePeerKeyDB.session_id == session_id,
                SpeechEvidencePeerKeyDB.state == "active",
            )
            .values(
                state="invalidated",
                version=SpeechEvidencePeerKeyDB.version + 1,
                updated_at_ms=int(self._clock_ms()),
            )
        )
        if sender_id is not None:
            statement = statement.where(SpeechEvidencePeerKeyDB.sender_id == sender_id)
        if before_epoch is not None:
            statement = statement.where(SpeechEvidencePeerKeyDB.epoch < before_epoch)
        with Session(engine) as session:
            result = session.exec(statement)
            session.commit()
            return int(result.rowcount)


class SqlSpeechEvidenceReplayWindow:
    """Database-backed implementation of the verifier's replay-window port."""

    def __init__(
        self,
        *,
        width: int = 256,
        maximum_contexts: int = 2048,
        ttl_ms: int = 60 * 60 * 1000,
        clock_ms=lambda: time.time_ns() // 1_000_000,
    ) -> None:
        if not 32 <= width <= 4096 or not 1 <= maximum_contexts <= 100_000 or ttl_ms < 60_000:
            raise ValueError("speech_replay_policy_invalid")
        self._width = width
        self._maximum = maximum_contexts
        self._ttl_ms = ttl_ms
        self._clock_ms = clock_ms

    def check(self, key: tuple[str, str, str, int, str], sequence: int) -> str | None:
        now = int(self._clock_ms())
        with Session(engine) as session:
            row = session.get(SpeechEvidenceReplayStateDB, _replay_id(key))
            if row is None or row.expires_at_ms <= now:
                return None
            return _replay_reason(int(row.highest_sequence), int(row.bitmap_hex, 16), self._width, sequence)

    def commit(self, key: tuple[str, str, str, int, str], sequence: int) -> None:
        if type(sequence) is not int or not 1 <= sequence <= MAX_SEQUENCE:
            raise SpeechEvidenceProtocolError("speech_evidence_sequence_invalid")
        now = int(self._clock_ms())
        identifier = _replay_id(key)
        try:
            with Session(engine) as session:
                session.exec(
                    delete(SpeechEvidenceReplayStateDB).where(SpeechEvidenceReplayStateDB.expires_at_ms <= now)
                )
                row = session.exec(
                    select(SpeechEvidenceReplayStateDB)
                    .where(SpeechEvidenceReplayStateDB.id == identifier)
                    .with_for_update()
                ).first()
                if row is None:
                    contexts = int(session.exec(select(func.count(SpeechEvidenceReplayStateDB.id))).one())
                    if contexts >= self._maximum:
                        raise SpeechEvidenceProtocolError("speech_evidence_replay_state_exhausted")
                    session.add(
                        SpeechEvidenceReplayStateDB(
                            id=identifier,
                            session_id=key[0],
                            pair_id=key[1],
                            sender_id=key[2],
                            epoch=key[3],
                            traffic_class=key[4],
                            highest_sequence=sequence,
                            bitmap_hex="1",
                            width=self._width,
                            expires_at_ms=now + self._ttl_ms,
                            updated_at_ms=now,
                        )
                    )
                else:
                    reason = _replay_reason(int(row.highest_sequence), int(row.bitmap_hex, 16), self._width, sequence)
                    if reason is not None:
                        raise SpeechEvidenceProtocolError(reason)
                    bitmap = int(row.bitmap_hex, 16)
                    highest = int(row.highest_sequence)
                    if sequence > highest:
                        bitmap = ((bitmap << (sequence - highest)) | 1) & ((1 << self._width) - 1)
                        highest = sequence
                    else:
                        bitmap |= 1 << (highest - sequence)
                    row.highest_sequence = highest
                    row.bitmap_hex = format(bitmap, "x")
                    row.expires_at_ms = now + self._ttl_ms
                    row.version += 1
                    row.updated_at_ms = now
                    session.add(row)
                session.commit()
        except IntegrityError as exc:
            # A competing Hub inserted the same context. Its committed claim
            # is authoritative; never reinterpret this request as fresh.
            reason = self.check(key, sequence) or "speech_evidence_replayed"
            raise SpeechEvidenceProtocolError(reason) from exc

    def advance_epoch(self, *, session_id: str, pair_id: str, minimum_epoch: int) -> None:
        if minimum_epoch < 1:
            raise ValueError("speech_replay_epoch_invalid")
        with Session(engine) as session:
            session.exec(
                delete(SpeechEvidenceReplayStateDB).where(
                    SpeechEvidenceReplayStateDB.session_id == session_id,
                    SpeechEvidenceReplayStateDB.pair_id == pair_id,
                    SpeechEvidenceReplayStateDB.epoch < minimum_epoch,
                )
            )
            session.commit()


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


@dataclass(frozen=True, slots=True)
class SpeechEvidenceTransferRecord:
    offer_id: str
    group_id: str
    state: str
    chunk_count: int
    acknowledged_chunks: int
    first_missing_index: int
    received_bytes: int
    in_flight_bytes: int
    expires_at_ms: int
    reason_code: str | None
    version: int

    def public_dict(self) -> dict[str, object]:
        return asdict(self)


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


def _public_key_bytes(value: str) -> bytes:
    try:
        raw = base64.b64decode(value, validate=True)
        if len(raw) != 32:
            raise ValueError
        Ed25519PublicKey.from_public_bytes(raw)
        return raw
    except (TypeError, ValueError) as exc:
        raise SpeechEvidenceSyncRepositoryError("speech_evidence_public_key_invalid", status_code=422) from exc


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


@contextmanager
def _offer_process_lock(offer_id: str) -> Iterator[None]:
    digest_value = hashlib.sha256(offer_id.encode("utf-8")).digest()
    lock = _OFFER_LOCK_STRIPES[int.from_bytes(digest_value[:2], "big") % len(_OFFER_LOCK_STRIPES)]
    with lock:
        yield


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


def _key_row(session: Session, *, lock: bool = False, **scope: object) -> SpeechEvidencePeerKeyDB | None:
    required = {"tenant_id", "session_id", "pair_id", "sender_id", "audience_id", "epoch", "key_id"}
    if set(scope) != required:
        return None
    statement = select(SpeechEvidencePeerKeyDB).where(
        SpeechEvidencePeerKeyDB.tenant_id == scope["tenant_id"],
        SpeechEvidencePeerKeyDB.session_id == scope["session_id"],
        SpeechEvidencePeerKeyDB.pair_id == scope["pair_id"],
        SpeechEvidencePeerKeyDB.sender_id == scope["sender_id"],
        SpeechEvidencePeerKeyDB.audience_id == scope["audience_id"],
        SpeechEvidencePeerKeyDB.epoch == scope["epoch"],
        SpeechEvidencePeerKeyDB.key_id == scope["key_id"],
    )
    if lock:
        statement = statement.with_for_update()
    return session.exec(statement).first()


def _key_record(row: SpeechEvidencePeerKeyDB) -> SpeechEvidencePeerKeyRecord:
    return SpeechEvidencePeerKeyRecord(
        tenant_id=row.tenant_id,
        session_id=row.session_id,
        pair_id=row.pair_id,
        sender_id=row.sender_id,
        audience_id=row.audience_id,
        epoch=int(row.epoch),
        key_id=row.key_id,
        public_key_b64=row.public_key_b64,
        fingerprint=row.fingerprint,
        membership_version=int(row.membership_version),
        consent_version=int(row.consent_version),
        expires_at_ms=int(row.expires_at_ms),
        state=row.state,
        version=int(row.version),
    )


def _replay_id(key: tuple[str, str, str, int, str]) -> str:
    return hashlib.sha256("\0".join(map(str, key)).encode()).hexdigest()


def _replay_reason(highest: int, bitmap: int, width: int, sequence: int) -> str | None:
    if sequence > highest:
        return None
    offset = highest - sequence
    if offset >= width:
        return "speech_evidence_sequence_stale"
    if bitmap & (1 << offset):
        return "speech_evidence_replayed"
    return None


def _offer_row(record: SpeechEvidenceOfferRecord, *, now_ms: int) -> SpeechEvidenceOfferDB:
    return SpeechEvidenceOfferDB(**_offer_values(record), created_at_ms=now_ms, updated_at_ms=now_ms)


def _offer_values(record: SpeechEvidenceOfferRecord) -> dict[str, Any]:
    return {
        "offer_id": record.offer_id,
        "tenant_id": record.tenant_id,
        "proposal_verification_digest": record.proposal_verification_digest,
        "acceptance_verification_digest": record.acceptance_verification_digest,
        "session_id": record.session_id,
        "pair_id": record.pair_id,
        "epoch": record.epoch,
        "sender_id": record.sender_id,
        "recipient_id": record.recipient_id,
        "inventory_root_digest": record.inventory_root_digest,
        "direction": record.direction,
        "purpose": record.purpose,
        "data_classes": list(record.data_classes),
        "fields": list(record.fields),
        "retention_seconds": record.retention_seconds,
        "trainer_class": record.trainer_class,
        "group_ids": list(record.group_ids),
        "group_previews": [row.public_dict() for row in record.group_previews],
        "group_preview_digest": record.group_preview_digest,
        "total_bytes": record.total_bytes,
        "sender_consent_digest": record.sender_consent_digest,
        "recipient_consent_digest": record.recipient_consent_digest,
        "scope_digest": record.scope_digest,
        "expires_at_ms": record.expires_at_ms,
        "state": record.state,
        "transfer_started": record.transfer_started,
        "invalidation_reason": record.invalidation_reason,
        "protocol_version": record.protocol_version,
        "version": record.version,
    }


def _offer_record(row: SpeechEvidenceOfferDB) -> SpeechEvidenceOfferRecord:
    return SpeechEvidenceOfferRecord(
        offer_id=row.offer_id,
        proposal_verification_digest=row.proposal_verification_digest,
        acceptance_verification_digest=row.acceptance_verification_digest,
        session_id=row.session_id,
        pair_id=row.pair_id,
        epoch=int(row.epoch),
        sender_id=row.sender_id,
        recipient_id=row.recipient_id,
        inventory_root_digest=row.inventory_root_digest,
        direction=row.direction,
        purpose=row.purpose,
        data_classes=tuple(str(value) for value in row.data_classes),
        fields=tuple(str(value) for value in row.fields),
        retention_seconds=int(row.retention_seconds),
        trainer_class=row.trainer_class,
        group_ids=tuple(str(value) for value in row.group_ids),
        group_previews=tuple(
            SpeechEvidenceGroupPreview.from_mapping(value)
            for value in (row.group_previews or [])
        ),
        group_preview_digest=str(row.group_preview_digest or ""),
        total_bytes=int(row.total_bytes),
        sender_consent_digest=row.sender_consent_digest,
        recipient_consent_digest=row.recipient_consent_digest,
        scope_digest=row.scope_digest,
        expires_at_ms=int(row.expires_at_ms),
        state=row.state,
        transfer_started=bool(row.transfer_started),
        invalidation_reason=row.invalidation_reason,
        tenant_id=row.tenant_id,
        version=int(row.version),
        protocol_version=str(row.protocol_version or "ananta.speech-evidence-sync.v1"),
    )


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
