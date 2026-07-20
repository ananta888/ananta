"""Envelope-key lifecycle for server-authorized speech evidence."""

from __future__ import annotations

import hashlib
import os
import time
from typing import Callable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.speech_evidence import SpeechEvidenceKeyDB
from ananta_contracts.speech_evidence_crypto import (
    SPEECH_EVIDENCE_ARTIFACT_CLASSES,
    SpeechEvidenceCryptoError,
    key_wrapping_aad,
)


class SpeechEvidenceKeyService:
    """Create one random DEK per artifact and keep only its wrapped form."""

    def __init__(
        self,
        *,
        master_key: bytes | None = None,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self._master_key = bytes(master_key) if master_key is not None else _configured_master_key()
        if len(self._master_key) < 32:
            raise SpeechEvidenceCryptoError(
                "speech_crypto_master_key_invalid",
                "evidence master key must provide at least 256 bits",
                status_code=500,
            )
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)

    def create(
        self,
        *,
        tenant_id: str,
        pair_id: str,
        purpose: str,
        artifact_class: str,
        artifact_ref: str,
        key_epoch: int,
    ) -> tuple[str, bytes]:
        if artifact_class not in SPEECH_EVIDENCE_ARTIFACT_CLASSES or key_epoch < 1:
            raise SpeechEvidenceCryptoError("speech_crypto_key_scope_invalid", "key scope is invalid")
        key_id = f"speech-key-{os.urandom(16).hex()}"
        dek = AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(12)
        wrapping_key = self._wrapping_key(tenant_id=tenant_id, pair_id=pair_id, purpose=purpose, key_epoch=key_epoch)
        aad = key_wrapping_aad(
            key_id=key_id,
            artifact_ref=artifact_ref,
            artifact_class=artifact_class,
            tenant_id=tenant_id,
            pair_id=pair_id,
            purpose=purpose,
            key_epoch=key_epoch,
        )
        wrapped = AESGCM(wrapping_key).encrypt(nonce, dek, aad)
        row = SpeechEvidenceKeyDB(
            id=key_id,
            tenant_id=tenant_id,
            pair_id=pair_id,
            purpose=purpose,
            artifact_class=artifact_class,
            artifact_ref=artifact_ref,
            key_epoch=key_epoch,
            wrapping_epoch=key_epoch,
            wrapped_dek=wrapped,
            wrapping_nonce=nonce,
            created_at_ms=self._clock_ms(),
        )
        try:
            with Session(engine) as session:
                session.add(row)
                session.commit()
        except IntegrityError as exc:
            raise SpeechEvidenceCryptoError(
                "speech_crypto_artifact_key_exists", "artifact already has a DEK", status_code=409
            ) from exc
        return key_id, dek

    def unwrap(
        self,
        key_id: str,
        *,
        tenant_id: str,
        pair_id: str,
        purpose: str,
        artifact_class: str,
        artifact_ref: str,
        key_epoch: int,
    ) -> bytes:
        with Session(engine) as session:
            row = session.exec(
                select(SpeechEvidenceKeyDB).where(
                    SpeechEvidenceKeyDB.id == key_id,
                    SpeechEvidenceKeyDB.tenant_id == tenant_id,
                )
            ).first()
        if row is None:
            raise SpeechEvidenceCryptoError(
                "speech_crypto_key_not_found", "evidence key was not found", status_code=404
            )
        bindings = (
            row.pair_id,
            row.purpose,
            row.artifact_class,
            row.artifact_ref,
            int(row.key_epoch),
        )
        if bindings != (pair_id, purpose, artifact_class, artifact_ref, key_epoch):
            raise SpeechEvidenceCryptoError(
                "speech_crypto_key_scope_mismatch", "evidence key binding does not match", status_code=403
            )
        if row.destroyed_at_ms is not None or row.wrapped_dek is None or row.wrapping_nonce is None:
            raise SpeechEvidenceCryptoError(
                "speech_crypto_key_destroyed", "evidence key has been destroyed", status_code=410
            )
        aad = key_wrapping_aad(
            key_id=row.id,
            artifact_ref=row.artifact_ref,
            artifact_class=row.artifact_class,
            tenant_id=row.tenant_id,
            pair_id=row.pair_id,
            purpose=row.purpose,
            key_epoch=int(row.wrapping_epoch),
        )
        try:
            return AESGCM(
                self._wrapping_key(
                    tenant_id=row.tenant_id,
                    pair_id=row.pair_id,
                    purpose=row.purpose,
                    key_epoch=int(row.wrapping_epoch),
                )
            ).decrypt(bytes(row.wrapping_nonce), bytes(row.wrapped_dek), aad)
        except InvalidTag as exc:
            raise SpeechEvidenceCryptoError(
                "speech_crypto_key_authentication_failed", "wrapped evidence key failed authentication", status_code=409
            ) from exc

    def rotate(
        self,
        key_id: str,
        *,
        tenant_id: str,
        pair_id: str,
        purpose: str,
        artifact_class: str,
        artifact_ref: str,
        current_key_epoch: int,
        next_key_epoch: int,
    ) -> None:
        if next_key_epoch <= current_key_epoch:
            raise SpeechEvidenceCryptoError("speech_crypto_epoch_stale", "key epoch must increase", status_code=409)
        with Session(engine) as session:
            current = session.exec(
                select(SpeechEvidenceKeyDB).where(
                    SpeechEvidenceKeyDB.id == key_id,
                    SpeechEvidenceKeyDB.tenant_id == tenant_id,
                    SpeechEvidenceKeyDB.wrapping_epoch == current_key_epoch,
                )
            ).first()
        if current is None:
            raise SpeechEvidenceCryptoError("speech_crypto_epoch_stale", "key rotation lost its fence", status_code=409)
        dek = self.unwrap(
            key_id,
            tenant_id=tenant_id,
            pair_id=pair_id,
            purpose=purpose,
            artifact_class=artifact_class,
            artifact_ref=artifact_ref,
            key_epoch=int(current.key_epoch),
        )
        nonce = os.urandom(12)
        aad = key_wrapping_aad(
            key_id=key_id,
            artifact_ref=artifact_ref,
            artifact_class=artifact_class,
            tenant_id=tenant_id,
            pair_id=pair_id,
            purpose=purpose,
            key_epoch=next_key_epoch,
        )
        wrapped = AESGCM(
            self._wrapping_key(tenant_id=tenant_id, pair_id=pair_id, purpose=purpose, key_epoch=next_key_epoch)
        ).encrypt(nonce, dek, aad)
        with Session(engine) as session:
            result = session.exec(
                update(SpeechEvidenceKeyDB)
                .where(
                    SpeechEvidenceKeyDB.id == key_id,
                    SpeechEvidenceKeyDB.tenant_id == tenant_id,
                    SpeechEvidenceKeyDB.wrapping_epoch == current_key_epoch,
                    SpeechEvidenceKeyDB.destroyed_at_ms.is_(None),
                )
                .values(
                    wrapping_epoch=next_key_epoch,
                    wrapped_dek=wrapped,
                    wrapping_nonce=nonce,
                    rotated_at_ms=self._clock_ms(),
                )
            )
            if result.rowcount != 1:
                session.rollback()
                raise SpeechEvidenceCryptoError(
                    "speech_crypto_epoch_stale", "key rotation lost its fence", status_code=409
                )
            session.commit()

    def destroy(self, key_id: str, *, tenant_id: str) -> bool:
        now = self._clock_ms()
        with Session(engine) as session:
            result = session.exec(
                update(SpeechEvidenceKeyDB)
                .where(
                    SpeechEvidenceKeyDB.id == key_id,
                    SpeechEvidenceKeyDB.tenant_id == tenant_id,
                    SpeechEvidenceKeyDB.destroyed_at_ms.is_(None),
                )
                .values(wrapped_dek=None, wrapping_nonce=None, destroyed_at_ms=now)
            )
            session.commit()
            return result.rowcount == 1

    def _wrapping_key(self, *, tenant_id: str, pair_id: str, purpose: str, key_epoch: int) -> bytes:
        info = (f"ananta-speech-evidence-wrap-v1\0{tenant_id}\0{pair_id}\0{purpose}\0{key_epoch}").encode("utf-8")
        return HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=hashlib.sha256(b"ananta-speech-evidence-wrap-salt-v1").digest(),
            info=info,
        ).derive(self._master_key)


def _configured_master_key() -> bytes:
    from agent.config import settings

    secret = str(settings.secret_key or "")
    if not secret:
        raise SpeechEvidenceCryptoError(
            "speech_crypto_master_key_missing", "speech evidence key material is not configured", status_code=500
        )
    return hashlib.sha256(f"ananta-speech-evidence-master-v1:{secret}".encode()).digest()


_service: SpeechEvidenceKeyService | None = None


def get_speech_evidence_key_service() -> SpeechEvidenceKeyService:
    global _service
    if _service is None:
        _service = SpeechEvidenceKeyService()
    return _service


__all__ = ["SpeechEvidenceKeyService", "get_speech_evidence_key_service"]
