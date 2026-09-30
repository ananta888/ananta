"""Durable current-epoch Ed25519 verification-key registry for speech-evidence peers."""

from __future__ import annotations

import base64
import hashlib
import re
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.speech_evidence_sync import SpeechEvidencePeerKeyDB
from agent.repositories.speech_evidence_sync_records import (
    SpeechEvidencePeerKeyRecord,
    SpeechEvidenceSyncRepositoryError,
)

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


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


def _public_key_bytes(value: str) -> bytes:
    try:
        raw = base64.b64decode(value, validate=True)
        if len(raw) != 32:
            raise ValueError
        Ed25519PublicKey.from_public_bytes(raw)
        return raw
    except (TypeError, ValueError) as exc:
        raise SpeechEvidenceSyncRepositoryError("speech_evidence_public_key_invalid", status_code=422) from exc


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


__all__ = ["SqlSpeechEvidencePeerKeyRegistry"]
