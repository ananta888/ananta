"""Authenticated-encryption envelope for temporary speech evidence."""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from typing import Mapping

from ananta_contracts.speech_evidence_governance import canonical_json

SPEECH_EVIDENCE_CIPHER_SCHEMA = "ananta.speech-evidence-ciphertext.v1"
SPEECH_EVIDENCE_ARTIFACT_CLASSES = frozenset(
    {"evidence", "manifest", "checkpoint", "curation_result", "evaluation_result"}
)
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}$")


class SpeechEvidenceCryptoError(RuntimeError):
    def __init__(self, reason_code: str, message: str, *, status_code: int = 422) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(message)


@dataclass(frozen=True)
class SpeechEvidenceCiphertext:
    artifact_ref: str
    artifact_class: str
    tenant_id: str
    pair_id: str
    purpose: str
    session_epoch: int
    key_epoch: int
    key_id: str
    content_digest: str
    nonce: bytes
    ciphertext: bytes
    algorithm: str = "AES-256-GCM"

    @property
    def aad(self) -> bytes:
        return canonical_json(self.aad_mapping())

    def aad_mapping(self) -> dict[str, object]:
        return {
            "schema": SPEECH_EVIDENCE_CIPHER_SCHEMA,
            "artifact_ref": self.artifact_ref,
            "artifact_class": self.artifact_class,
            "tenant_id": self.tenant_id,
            "pair_id": self.pair_id,
            "purpose": self.purpose,
            "session_epoch": self.session_epoch,
            "key_epoch": self.key_epoch,
            "key_id": self.key_id,
            "content_digest": self.content_digest,
            "algorithm": self.algorithm,
        }

    def to_storage_dict(self) -> dict[str, object]:
        return {
            **self.aad_mapping(),
            "nonce": base64.b64encode(self.nonce).decode("ascii"),
            "ciphertext": base64.b64encode(self.ciphertext).decode("ascii"),
        }

    @classmethod
    def from_mapping(cls, raw: object, *, max_ciphertext_bytes: int = 16 * 1024 * 1024) -> "SpeechEvidenceCiphertext":
        if not isinstance(raw, Mapping) or any(not isinstance(key, str) for key in raw):
            raise SpeechEvidenceCryptoError("speech_crypto_envelope_invalid", "cipher envelope must be an object")
        data = dict(raw)
        required = {
            "schema",
            "artifact_ref",
            "artifact_class",
            "tenant_id",
            "pair_id",
            "purpose",
            "session_epoch",
            "key_epoch",
            "key_id",
            "content_digest",
            "algorithm",
            "nonce",
            "ciphertext",
        }
        if set(data) != required:
            raise SpeechEvidenceCryptoError(
                "speech_crypto_envelope_fields_invalid", "cipher envelope fields do not match the v1 contract"
            )
        if data["schema"] != SPEECH_EVIDENCE_CIPHER_SCHEMA or data["algorithm"] != "AES-256-GCM":
            raise SpeechEvidenceCryptoError("speech_crypto_algorithm_invalid", "cipher algorithm is unsupported")
        artifact_class = str(data["artifact_class"])
        if artifact_class not in SPEECH_EVIDENCE_ARTIFACT_CLASSES:
            raise SpeechEvidenceCryptoError("speech_crypto_artifact_class_invalid", "artifact class is unsupported")
        nonce = _decode(data["nonce"], "nonce")
        ciphertext = _decode(data["ciphertext"], "ciphertext")
        if len(nonce) != 12:
            raise SpeechEvidenceCryptoError("speech_crypto_nonce_invalid", "AES-GCM nonce must be 96 bits")
        if not 16 <= len(ciphertext) <= max_ciphertext_bytes:
            raise SpeechEvidenceCryptoError("speech_crypto_ciphertext_size_invalid", "ciphertext size is invalid")
        content_digest = str(data["content_digest"])
        if _DIGEST.fullmatch(content_digest) is None:
            raise SpeechEvidenceCryptoError("speech_crypto_digest_invalid", "content digest is invalid")
        return cls(
            artifact_ref=_safe(data["artifact_ref"], "artifact_ref"),
            artifact_class=artifact_class,
            tenant_id=_safe(data["tenant_id"], "tenant_id"),
            pair_id=_safe(data["pair_id"], "pair_id"),
            purpose=_safe(data["purpose"], "purpose"),
            session_epoch=_integer(data["session_epoch"], "session_epoch", minimum=1),
            key_epoch=_integer(data["key_epoch"], "key_epoch", minimum=1),
            key_id=_safe(data["key_id"], "key_id"),
            content_digest=content_digest,
            nonce=nonce,
            ciphertext=ciphertext,
        )


def key_wrapping_aad(
    *,
    key_id: str,
    artifact_ref: str,
    artifact_class: str,
    tenant_id: str,
    pair_id: str,
    purpose: str,
    key_epoch: int,
) -> bytes:
    return canonical_json(
        {
            "schema": "ananta.speech-evidence-key-wrap.v1",
            "key_id": key_id,
            "artifact_ref": artifact_ref,
            "artifact_class": artifact_class,
            "tenant_id": tenant_id,
            "pair_id": pair_id,
            "purpose": purpose,
            "key_epoch": key_epoch,
        }
    )


def _decode(value: object, field: str) -> bytes:
    if not isinstance(value, str):
        raise SpeechEvidenceCryptoError("speech_crypto_base64_invalid", f"{field} is not base64")
    try:
        return base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise SpeechEvidenceCryptoError("speech_crypto_base64_invalid", f"{field} is not base64") from exc


def _safe(value: object, field: str) -> str:
    text = value if isinstance(value, str) else ""
    if _SAFE.fullmatch(text) is None or ".." in text.split("/"):
        raise SpeechEvidenceCryptoError("speech_crypto_binding_invalid", f"{field} is invalid")
    return text


def _integer(value: object, field: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SpeechEvidenceCryptoError("speech_crypto_binding_invalid", f"{field} is invalid")
    return value


__all__ = [
    "SPEECH_EVIDENCE_ARTIFACT_CLASSES",
    "SPEECH_EVIDENCE_CIPHER_SCHEMA",
    "SpeechEvidenceCiphertext",
    "SpeechEvidenceCryptoError",
    "key_wrapping_aad",
]
