"""DIP boundary and AES-GCM implementation for speech evidence payloads."""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from agent.services.speech_evidence_key_service import (
    SpeechEvidenceKeyService,
    get_speech_evidence_key_service,
)
from ananta_contracts.speech_evidence_crypto import SpeechEvidenceCiphertext, SpeechEvidenceCryptoError


class SpeechEvidenceEncryptionPort(Protocol):
    def encrypt(self, plaintext: bytes, **bindings: object) -> SpeechEvidenceCiphertext: ...

    def decrypt(self, envelope: SpeechEvidenceCiphertext, *, security_mode: str) -> bytes: ...

    def destroy(self, key_id: str, *, tenant_id: str) -> bool: ...


class AesGcmSpeechEvidenceEncryption:
    MAX_PLAINTEXT_BYTES = 16 * 1024 * 1024

    def __init__(self, keys: SpeechEvidenceKeyService | None = None) -> None:
        self._keys = keys or get_speech_evidence_key_service()

    def encrypt(
        self,
        plaintext: bytes,
        *,
        artifact_ref: str,
        artifact_class: str,
        tenant_id: str,
        pair_id: str,
        purpose: str,
        session_epoch: int,
        key_epoch: int,
        security_mode: str = "trusted_compute",
    ) -> SpeechEvidenceCiphertext:
        self._server_mode(security_mode)
        payload = bytes(plaintext)
        if not payload or len(payload) > self.MAX_PLAINTEXT_BYTES:
            raise SpeechEvidenceCryptoError(
                "speech_crypto_plaintext_size_invalid", "speech evidence payload size is invalid"
            )
        key_id, dek = self._keys.create(
            tenant_id=tenant_id,
            pair_id=pair_id,
            purpose=purpose,
            artifact_class=artifact_class,
            artifact_ref=artifact_ref,
            key_epoch=key_epoch,
        )
        nonce = os.urandom(12)
        envelope = SpeechEvidenceCiphertext(
            artifact_ref=artifact_ref,
            artifact_class=artifact_class,
            tenant_id=tenant_id,
            pair_id=pair_id,
            purpose=purpose,
            session_epoch=session_epoch,
            key_epoch=key_epoch,
            key_id=key_id,
            # Keyed digest prevents equality correlation between artifacts.
            content_digest=hmac.new(dek, payload, hashlib.sha256).hexdigest(),
            nonce=nonce,
            ciphertext=b"pending-authentication-tag",
        )
        ciphertext = AESGCM(dek).encrypt(nonce, payload, envelope.aad)
        return SpeechEvidenceCiphertext(**{**envelope.__dict__, "ciphertext": ciphertext})

    def decrypt(self, envelope: SpeechEvidenceCiphertext, *, security_mode: str = "trusted_compute") -> bytes:
        self._server_mode(security_mode)
        dek = self._keys.unwrap(
            envelope.key_id,
            tenant_id=envelope.tenant_id,
            pair_id=envelope.pair_id,
            purpose=envelope.purpose,
            artifact_class=envelope.artifact_class,
            artifact_ref=envelope.artifact_ref,
            key_epoch=envelope.key_epoch,
        )
        try:
            plaintext = AESGCM(dek).decrypt(envelope.nonce, envelope.ciphertext, envelope.aad)
        except InvalidTag as exc:
            raise SpeechEvidenceCryptoError(
                "speech_crypto_authentication_failed", "speech evidence authentication failed", status_code=409
            ) from exc
        if hmac.new(dek, plaintext, hashlib.sha256).hexdigest() != envelope.content_digest:
            raise SpeechEvidenceCryptoError(
                "speech_crypto_content_digest_mismatch", "decrypted evidence digest does not match", status_code=409
            )
        return plaintext

    def destroy(self, key_id: str, *, tenant_id: str) -> bool:
        return self._keys.destroy(key_id, tenant_id=tenant_id)

    @staticmethod
    def _server_mode(security_mode: str) -> None:
        if security_mode == "strict_e2ee":
            raise SpeechEvidenceCryptoError(
                "speech_crypto_strict_e2ee_server_forbidden",
                "strict E2EE evidence cannot be decrypted or encrypted by Hub workers",
                status_code=403,
            )
        if security_mode != "trusted_compute":
            raise SpeechEvidenceCryptoError(
                "speech_crypto_security_mode_invalid", "speech evidence security mode is invalid"
            )


class UnavailableSpeechEvidenceEncryption:
    """Fail-closed port used when a deployment has no evidence KMS binding."""

    def __getattr__(self, _name: str):
        def unavailable(*_args, **_kwargs):
            raise SpeechEvidenceCryptoError(
                "speech_crypto_port_unavailable", "speech evidence encryption is unavailable", status_code=503
            )

        return unavailable


_encryption: AesGcmSpeechEvidenceEncryption | None = None


def get_speech_evidence_encryption_port() -> AesGcmSpeechEvidenceEncryption:
    global _encryption
    if _encryption is None:
        _encryption = AesGcmSpeechEvidenceEncryption()
    return _encryption


__all__ = [
    "AesGcmSpeechEvidenceEncryption",
    "SpeechEvidenceEncryptionPort",
    "UnavailableSpeechEvidenceEncryption",
    "get_speech_evidence_encryption_port",
]
