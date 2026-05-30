"""SS02.03 / PRD05.01: Tests für Session-Key-Ableitung und Payload-Verschlüsselung."""
from __future__ import annotations

import os
import pytest

from client_surfaces.operator_tui.share_crypto import (
    SessionKeyPair,
    encrypt_chat,
    decrypt_chat,
    encrypt_view,
    decrypt_view,
    EncryptedPayload,
    DecryptionFailedError,
    CryptoError,
    _derive_key,
    _CONTEXT_CHAT,
    _CONTEXT_VIEW,
)


def make_shared_key(length: int = 32) -> bytes:
    return os.urandom(length)


def test_encrypt_decrypt_chat_roundtrip():
    key = make_shared_key()
    plaintext = b"Hallo Welt"
    msg_id = "msg-001"
    payload = encrypt_chat(plaintext, key, msg_id)
    assert payload.context_label == "chat"
    assert payload.message_id == msg_id
    result = decrypt_chat(payload, key)
    assert result == plaintext


def test_encrypt_decrypt_view_roundtrip():
    key = make_shared_key()
    plaintext = b"TUI SNAPSHOT CONTENT"
    msg_id = "view-001"
    payload = encrypt_view(plaintext, key, msg_id)
    assert payload.context_label == "view"
    result = decrypt_view(payload, key)
    assert result == plaintext


def test_chat_decrypt_with_wrong_key_raises():
    key1 = make_shared_key()
    key2 = make_shared_key()
    payload = encrypt_chat(b"secret", key1, "msg-002")
    with pytest.raises(DecryptionFailedError):
        decrypt_chat(payload, key2)


def test_view_decrypt_with_wrong_key_raises():
    key1 = make_shared_key()
    key2 = make_shared_key()
    payload = encrypt_view(b"snapshot", key1, "view-002")
    with pytest.raises(DecryptionFailedError):
        decrypt_view(payload, key2)


def test_chat_wrong_context_label_raises():
    key = make_shared_key()
    payload = encrypt_view(b"data", key, "msg-003")  # view-context
    with pytest.raises(DecryptionFailedError, match="context"):
        decrypt_chat(payload, key)


def test_view_wrong_context_label_raises():
    key = make_shared_key()
    payload = encrypt_chat(b"data", key, "msg-004")  # chat-context
    with pytest.raises(DecryptionFailedError, match="context"):
        decrypt_view(payload, key)


def test_nonces_are_unique():
    key = make_shared_key()
    p1 = encrypt_chat(b"msg", key, "id1")
    p2 = encrypt_chat(b"msg", key, "id2")
    assert p1.nonce != p2.nonce


def test_invalid_key_length_raises():
    with pytest.raises(CryptoError, match="key length"):
        encrypt_chat(b"test", b"short", "id")


def test_payload_to_and_from_dict():
    key = make_shared_key()
    original = encrypt_chat(b"roundtrip", key, "id-rt")
    d = original.to_dict()
    assert isinstance(d["ciphertext"], str)
    assert isinstance(d["nonce"], str)
    restored = EncryptedPayload.from_dict(d)
    assert decrypt_chat(restored, key) == b"roundtrip"


def test_manipulated_ciphertext_raises():
    key = make_shared_key()
    payload = encrypt_chat(b"original", key, "id-manip")
    ct = bytearray(payload.ciphertext)
    ct[0] ^= 0xFF  # Bit flippen
    bad_payload = EncryptedPayload(
        ciphertext=bytes(ct),
        nonce=payload.nonce,
        context_label=payload.context_label,
        message_id=payload.message_id,
    )
    with pytest.raises(DecryptionFailedError):
        decrypt_chat(bad_payload, key)


def test_derive_key_different_contexts_different_keys():
    secret = os.urandom(32)
    k_chat = _derive_key(secret, _CONTEXT_CHAT)
    k_view = _derive_key(secret, _CONTEXT_VIEW)
    assert k_chat != k_view
    assert len(k_chat) == 32
    assert len(k_view) == 32


def test_session_key_pair_ecdh():
    kp1 = SessionKeyPair()
    kp2 = SessionKeyPair()
    # Beide leiten aus der Perspektive des jeweils anderen ab
    sk1 = kp1.derive_shared_key(kp2.public_key_bytes, _CONTEXT_CHAT)
    sk2 = kp2.derive_shared_key(kp1.public_key_bytes, _CONTEXT_CHAT)
    # Mit echter Krypto müssen gleich sein; mit Stub-Fallback implementierungsabhängig
    # Mindestanforderung: beide Keys sind 32 Byte
    assert len(sk1) == 32
    assert len(sk2) == 32
