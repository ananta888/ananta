from __future__ import annotations

import base64
import json
import time
from types import SimpleNamespace

import pytest

from agent.services.share_view_security_service import ShareViewSecurityError, ShareViewSecurityService
from ananta_contracts.webrtc_security import (
    AuthenticatedMetadata,
    EnvelopeRecipient,
    EnvelopeScope,
    SecureEnvelopeV1,
    seal_secure_envelope,
)


def _sealed(*, scope_id: str, sender_id: str, epoch: int, sequence: int) -> SecureEnvelopeV1:
    pending = SecureEnvelopeV1(
        version=1,
        scope=EnvelopeScope("session", scope_id),
        sender_id=sender_id,
        recipient=EnvelopeRecipient("peer", "bob"),
        epoch=epoch,
        sequence=sequence,
        key_id="pair-key-1",
        payload_type="pair.view_delta",
        expires_at_ms=int(time.time() * 1000) + 60_000,
        nonce_b64=base64.b64encode(b"n" * 12).decode(),
        aad=AuthenticatedMetadata("semantic", "json", "a" * 64),
        ciphertext_b64="",
    )
    return seal_secure_envelope(key=b"k" * 32, plaintext=b"{}", envelope=pending)


class Epochs:
    @staticmethod
    def current_epoch(_kind, _scope):
        return 3

    @staticmethod
    def accept_sequence(**values):
        return SimpleNamespace(
            accepted=values["authenticated_sender_id"] == values["sender_id"],
            reason_code="accepted" if values["authenticated_sender_id"] == values["sender_id"] else "sender_mismatch",
        )


def test_view_security_service_owns_scope_epoch_and_replay_validation() -> None:
    service = ShareViewSecurityService(Epochs())
    envelope = _sealed(scope_id="session-a", sender_id="alice", epoch=3, sequence=1)
    service.validate(
        session_id="session-a",
        authenticated_sender_id="alice",
        serialized=json.dumps(envelope.to_dict()),
    )
    with pytest.raises(ShareViewSecurityError, match="sender_mismatch"):
        service.validate(
            session_id="session-a",
            authenticated_sender_id="mallory",
            serialized=json.dumps(envelope.to_dict()),
        )
