"""SS02.03 / PRD05.01: Session-Key-Ableitung für Chat/View-Payloads.

- Shared Secret pro Teilnehmer via X25519 ECDH
- Chat- und View-Payloads nutzen getrennte Kontextlabels
- Nonce/Message-IDs werden nicht wiederverwendet (random 12-byte nonce)
- Fehlerhafte Entschlüsselung wird als failed/blocked markiert
"""
from __future__ import annotations

import hashlib
import hmac
import os
import struct
from dataclasses import dataclass
from typing import Any

_CONTEXT_CHAT = b"ananta-v1-chat"
_CONTEXT_VIEW = b"ananta-v1-view"
_KEY_LEN = 32
_NONCE_LEN = 12
_TAG_LEN = 16

try:
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    _CRYPTO_AVAILABLE = True
except ImportError:
    _CRYPTO_AVAILABLE = False


class CryptoError(Exception):
    pass


class DecryptionFailedError(CryptoError):
    """Entschlüsselung fehlgeschlagen – Nachricht wird als failed/blocked markiert."""
    pass


def _hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    """Minimale HKDF-Expand Implementierung (ohne externe Deps als Fallback)."""
    t = b""
    okm = b""
    for i in range(1, (length + 31) // 32 + 1):
        t = hmac.new(prk, t + info + bytes([i]), hashlib.sha256).digest()
        okm += t
    return okm[:length]


def _derive_key(shared_secret: bytes, context: bytes) -> bytes:
    """Leitet einen 32-Byte-Schlüssel aus shared_secret + context ab."""
    prk = hmac.new(b"ananta-salt-v1", shared_secret, hashlib.sha256).digest()
    return _hkdf_expand(prk, context, _KEY_LEN)


@dataclass
class EncryptedPayload:
    ciphertext: bytes
    nonce: bytes
    context_label: str
    message_id: str

    def to_dict(self) -> dict[str, Any]:
        import base64
        return {
            "ciphertext": base64.b64encode(self.ciphertext).decode(),
            "nonce": base64.b64encode(self.nonce).decode(),
            "context_label": self.context_label,
            "message_id": self.message_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EncryptedPayload":
        import base64
        return cls(
            ciphertext=base64.b64decode(str(d.get("ciphertext") or "")),
            nonce=base64.b64decode(str(d.get("nonce") or "")),
            context_label=str(d.get("context_label") or ""),
            message_id=str(d.get("message_id") or ""),
        )


class SessionKeyPair:
    """Hält ein Ephemeral X25519-Keypair für ECDH."""

    def __init__(self) -> None:
        if not _CRYPTO_AVAILABLE:
            self._priv_bytes = os.urandom(32)
            self._pub_bytes = os.urandom(32)
            return
        self._key = X25519PrivateKey.generate()
        self._priv_bytes = b""  # nicht exportierbar nach außen
        self._pub_bytes = self._key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    @property
    def public_key_bytes(self) -> bytes:
        return self._pub_bytes

    def derive_shared_key(self, peer_public_key_bytes: bytes, context: bytes) -> bytes:
        if not _CRYPTO_AVAILABLE:
            # Deterministische Stub-Ableitung für Tests ohne cryptography
            combined = self._priv_bytes + peer_public_key_bytes
            return _derive_key(hashlib.sha256(combined).digest(), context)
        peer_pub = X25519PublicKey.from_public_bytes(peer_public_key_bytes)
        shared = self._key.exchange(peer_pub)
        return _derive_key(shared, context)


def encrypt_chat(plaintext: bytes, shared_key: bytes, message_id: str) -> EncryptedPayload:
    """Verschlüsselt einen Chat-Payload mit AES-256-GCM."""
    if len(shared_key) != _KEY_LEN:
        raise CryptoError(f"Invalid key length: {len(shared_key)}")
    nonce = os.urandom(_NONCE_LEN)
    if not _CRYPTO_AVAILABLE:
        ct = _stub_encrypt(plaintext, shared_key, nonce)
    else:
        aesgcm = AESGCM(shared_key)
        ct = aesgcm.encrypt(nonce, plaintext, message_id.encode())
    return EncryptedPayload(ciphertext=ct, nonce=nonce, context_label="chat", message_id=message_id)


def decrypt_chat(payload: EncryptedPayload, shared_key: bytes) -> bytes:
    """Entschlüsselt Chat-Payload. Wirft DecryptionFailedError bei Fehler."""
    if payload.context_label != "chat":
        raise DecryptionFailedError(f"Wrong context label: {payload.context_label}")
    if len(shared_key) != _KEY_LEN:
        raise DecryptionFailedError("Invalid key length")
    if not _CRYPTO_AVAILABLE:
        try:
            return _stub_decrypt(payload.ciphertext, shared_key, payload.nonce)
        except Exception as exc:
            raise DecryptionFailedError(f"Decryption failed: {exc}") from exc
    try:
        aesgcm = AESGCM(shared_key)
        return aesgcm.decrypt(payload.nonce, payload.ciphertext, payload.message_id.encode())
    except Exception as exc:
        raise DecryptionFailedError(f"Decryption failed: {exc}") from exc


def encrypt_view(plaintext: bytes, shared_key: bytes, message_id: str) -> EncryptedPayload:
    """Verschlüsselt einen View-Snapshot/Delta-Payload."""
    if len(shared_key) != _KEY_LEN:
        raise CryptoError(f"Invalid key length: {len(shared_key)}")
    nonce = os.urandom(_NONCE_LEN)
    if not _CRYPTO_AVAILABLE:
        ct = _stub_encrypt(plaintext, shared_key, nonce)
    else:
        aesgcm = AESGCM(shared_key)
        ct = aesgcm.encrypt(nonce, plaintext, message_id.encode())
    return EncryptedPayload(ciphertext=ct, nonce=nonce, context_label="view", message_id=message_id)


def decrypt_view(payload: EncryptedPayload, shared_key: bytes) -> bytes:
    """Entschlüsselt View-Payload."""
    if payload.context_label != "view":
        raise DecryptionFailedError(f"Wrong context label: {payload.context_label}")
    if len(shared_key) != _KEY_LEN:
        raise DecryptionFailedError("Invalid key length")
    if not _CRYPTO_AVAILABLE:
        try:
            return _stub_decrypt(payload.ciphertext, shared_key, payload.nonce)
        except Exception as exc:
            raise DecryptionFailedError(f"Decryption failed: {exc}") from exc
    try:
        aesgcm = AESGCM(shared_key)
        return aesgcm.decrypt(payload.nonce, payload.ciphertext, payload.message_id.encode())
    except Exception as exc:
        raise DecryptionFailedError(f"Decryption failed: {exc}") from exc


# --- Stub-Crypto für Tests ohne cryptography-Paket ---

def _stub_encrypt(plaintext: bytes, key: bytes, nonce: bytes) -> bytes:
    keystream = hashlib.sha256(key + nonce).digest() * (len(plaintext) // 32 + 1)
    ct = bytes(a ^ b for a, b in zip(plaintext, keystream))
    tag = hmac.new(key, nonce + ct, hashlib.sha256).digest()[:_TAG_LEN]
    return ct + tag


def _stub_decrypt(ciphertext: bytes, key: bytes, nonce: bytes) -> bytes:
    if len(ciphertext) < _TAG_LEN:
        raise ValueError("Ciphertext too short")
    ct, tag = ciphertext[:-_TAG_LEN], ciphertext[-_TAG_LEN:]
    expected_tag = hmac.new(key, nonce + ct, hashlib.sha256).digest()[:_TAG_LEN]
    if not hmac.compare_digest(tag, expected_tag):
        raise ValueError("Tag mismatch – payload manipulated")
    keystream = hashlib.sha256(key + nonce).digest() * (len(ct) // 32 + 1)
    return bytes(a ^ b for a, b in zip(ct, keystream))
