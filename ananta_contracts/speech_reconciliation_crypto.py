"""Shared, implementation-neutral crypto for reconciliation artifact transfer.

The Hub seals one immutable, attempt-bound audio bundle and the isolated
worker opens it.  Keeping the primitive in the shared contract package avoids
either side importing the other runtime while making byte-level conformance
testable.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ananta_contracts.speech_reconciliation import canonical_json
from ananta_contracts.speech_reconciliation_worker import SpeechReconciliationAudioArtifact

KEYRING_SCHEMA = "ananta.speech-reconciliation-keyring.v1"
NONCE_BYTES = 12
TAG_BYTES = 16
MAX_KEYRING_BYTES = 64 * 1024
MAX_KEY_EPOCHS = 16


class SpeechReconciliationCryptoError(RuntimeError):
    """Content-free error safe to return across a control boundary."""

    def __init__(self, reason_code: str, *, retryable: bool = False) -> None:
        self.reason_code = reason_code
        self.retryable = retryable
        super().__init__(reason_code)


class SpeechReconciliationEpochKeyring:
    """Bounded read-only epoch key source shared by Hub and worker."""

    def __init__(self, keys: Mapping[int, bytes]) -> None:
        try:
            normalized = {int(epoch): bytes(key) for epoch, key in keys.items()}
        except (TypeError, ValueError) as exc:
            raise SpeechReconciliationCryptoError("speech_reconciliation_keyring_invalid") from exc
        if (
            not 1 <= len(normalized) <= MAX_KEY_EPOCHS
            or any(epoch < 1 or len(key) != 32 for epoch, key in normalized.items())
        ):
            raise SpeechReconciliationCryptoError("speech_reconciliation_keyring_invalid")
        self._keys = normalized

    @classmethod
    def from_file(cls, path: str | Path) -> "SpeechReconciliationEpochKeyring":
        unresolved = Path(path).absolute()
        try:
            resolved = unresolved.resolve(strict=True)
            metadata = unresolved.stat()
        except OSError as exc:
            raise SpeechReconciliationCryptoError("speech_reconciliation_keyring_invalid") from exc
        if (
            unresolved.is_symlink()
            or resolved != unresolved
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size > MAX_KEYRING_BYTES
            or metadata.st_nlink != 1
            or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        ):
            raise SpeechReconciliationCryptoError(
                "speech_reconciliation_keyring_boundary_violation"
            )
        try:
            raw = json.loads(unresolved.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SpeechReconciliationCryptoError("speech_reconciliation_keyring_invalid") from exc
        if (
            not isinstance(raw, Mapping)
            or set(raw) != {"schema", "keys"}
            or raw["schema"] != KEYRING_SCHEMA
            or not isinstance(raw["keys"], Mapping)
            or not 1 <= len(raw["keys"]) <= MAX_KEY_EPOCHS
        ):
            raise SpeechReconciliationCryptoError("speech_reconciliation_keyring_invalid")
        keys: dict[int, bytes] = {}
        try:
            for raw_epoch, encoded in raw["keys"].items():
                epoch = int(str(raw_epoch))
                if isinstance(raw_epoch, bool) or not isinstance(encoded, str):
                    raise ValueError
                decoded = base64.b64decode(encoded, validate=True)
                if epoch in keys:
                    raise ValueError
                keys[epoch] = decoded
        except (TypeError, ValueError) as exc:
            raise SpeechReconciliationCryptoError("speech_reconciliation_keyring_invalid") from exc
        return cls(keys)

    def resolve(self, *, key_epoch: int, artifact_ref: str) -> bytes:
        if not artifact_ref:
            raise SpeechReconciliationCryptoError("speech_reconciliation_artifact_key_invalid")
        key = self._keys.get(key_epoch)
        if key is None:
            raise SpeechReconciliationCryptoError(
                "speech_reconciliation_key_epoch_unavailable",
                retryable=True,
            )
        return key


def derive_speech_reconciliation_artifact_key(
    root_key: bytes,
    *,
    key_epoch: int,
    artifact_ref: str,
) -> bytes:
    """Derive a key scoped to one immutable artifact reference and epoch."""

    if len(root_key) != 32 or key_epoch < 1 or not artifact_ref:
        raise SpeechReconciliationCryptoError("speech_reconciliation_artifact_key_invalid")
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=hashlib.sha256(b"ananta.speech-reconciliation.audio.v1").digest(),
        info=f"{key_epoch}\0{artifact_ref}".encode(),
    ).derive(bytes(root_key))


def seal_speech_reconciliation_audio(
    *,
    root_key: bytes,
    artifact: SpeechReconciliationAudioArtifact,
    job,
    plaintext: bytes,
    nonce: bytes | None = None,
) -> bytes:
    """Seal a fully-described artifact with every immutable job binding as AAD."""

    payload = bytes(plaintext)
    if len(payload) != artifact.plaintext_bytes:
        raise SpeechReconciliationCryptoError(
            "speech_reconciliation_artifact_plaintext_size_mismatch"
        )
    if hashlib.sha256(payload).hexdigest() != artifact.content_digest:
        raise SpeechReconciliationCryptoError("speech_reconciliation_artifact_content_tamper")
    if artifact.key_epoch != job.key_epoch:
        raise SpeechReconciliationCryptoError("speech_reconciliation_key_epoch_mismatch")
    nonce_bytes = os.urandom(NONCE_BYTES) if nonce is None else bytes(nonce)
    if len(nonce_bytes) != NONCE_BYTES:
        raise SpeechReconciliationCryptoError("speech_reconciliation_artifact_nonce_invalid")
    key = derive_speech_reconciliation_artifact_key(
        root_key,
        key_epoch=job.key_epoch,
        artifact_ref=artifact.artifact_ref,
    )
    ciphertext = nonce_bytes + AESGCM(key).encrypt(
        nonce_bytes,
        payload,
        canonical_json(artifact.aad_mapping(job)),
    )
    if len(ciphertext) != artifact.ciphertext_bytes:
        raise SpeechReconciliationCryptoError(
            "speech_reconciliation_artifact_ciphertext_size_mismatch"
        )
    return ciphertext


def open_speech_reconciliation_audio(
    *,
    root_key: bytes,
    artifact: SpeechReconciliationAudioArtifact,
    job,
    ciphertext: bytes,
) -> bytes:
    """Authenticate and open a nonce-prefixed artifact sealed by the Hub."""

    payload = bytes(ciphertext)
    if len(payload) < NONCE_BYTES + TAG_BYTES or len(payload) != artifact.ciphertext_bytes:
        raise SpeechReconciliationCryptoError("speech_reconciliation_artifact_truncated")
    if artifact.key_epoch != job.key_epoch:
        raise SpeechReconciliationCryptoError("speech_reconciliation_key_epoch_mismatch")
    key = derive_speech_reconciliation_artifact_key(
        root_key,
        key_epoch=job.key_epoch,
        artifact_ref=artifact.artifact_ref,
    )
    try:
        plaintext = AESGCM(key).decrypt(
            payload[:NONCE_BYTES],
            payload[NONCE_BYTES:],
            canonical_json(artifact.aad_mapping(job)),
        )
    except InvalidTag as exc:
        raise SpeechReconciliationCryptoError(
            "speech_reconciliation_artifact_authentication_failed"
        ) from exc
    if (
        len(plaintext) != artifact.plaintext_bytes
        or hashlib.sha256(plaintext).hexdigest() != artifact.content_digest
    ):
        raise SpeechReconciliationCryptoError("speech_reconciliation_artifact_content_tamper")
    return plaintext


__all__ = [
    "KEYRING_SCHEMA",
    "NONCE_BYTES",
    "SpeechReconciliationCryptoError",
    "SpeechReconciliationEpochKeyring",
    "derive_speech_reconciliation_artifact_key",
    "open_speech_reconciliation_audio",
    "seal_speech_reconciliation_audio",
]
