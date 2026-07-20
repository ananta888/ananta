"""Strict Pair View envelope validation outside Flask route handlers."""

from __future__ import annotations

import json
from collections.abc import Callable, Collection, Mapping
from typing import Protocol

from ananta_contracts.webrtc_security import (
    SecureEnvelopeError,
    SecureEnvelopeV1,
    validate_secure_envelope,
)


class ShareViewEpochPort(Protocol):
    def current_epoch(self, scope_kind: str, scope_id: str) -> int | None: ...

    def accept_sequence(self, **values): ...


class ShareViewSecurityError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


class ShareSecureEnvelopeService:
    """Admit opaque pair envelopes before an authoritative relay mutation.

    Payload plaintext remains unavailable to the Hub.  The caller supplies a
    narrow authorization callback for current membership, permission and
    bidirectional key-confirmation checks; replay state is consumed only after
    that callback succeeds.
    """

    def __init__(self, epochs: ShareViewEpochPort) -> None:
        self._epochs = epochs

    def validate(
        self,
        *,
        session_id: str,
        authenticated_sender_id: str,
        serialized: object,
        allowed_payload_types: Collection[str] | None = None,
        traffic_by_payload: Mapping[str, str] | None = None,
        expected_contract_digest: str | None = None,
        authorizer: Callable[[SecureEnvelopeV1], None] | None = None,
    ) -> SecureEnvelopeV1:
        if not isinstance(serialized, str):
            raise ShareViewSecurityError("secure_envelope_required", status_code=400)
        try:
            raw = json.loads(serialized)
            secure = validate_secure_envelope(raw)
        except json.JSONDecodeError as exc:
            raise ShareViewSecurityError("envelope_json_invalid", status_code=400) from exc
        except SecureEnvelopeError as exc:
            raise ShareViewSecurityError(exc.reason_code, status_code=400) from exc
        if secure.scope.kind != "session" or secure.scope.id != session_id:
            raise ShareViewSecurityError("scope_mismatch", status_code=403)
        if secure.recipient.kind != "peer" or secure.recipient.id == authenticated_sender_id:
            raise ShareViewSecurityError("recipient_mismatch", status_code=403)
        if allowed_payload_types is not None and secure.payload_type not in frozenset(allowed_payload_types):
            raise ShareViewSecurityError("payload_type_not_authorized", status_code=403)
        if traffic_by_payload is not None and traffic_by_payload.get(secure.payload_type) != secure.aad.traffic_class:
            raise ShareViewSecurityError("traffic_class_mismatch", status_code=403)
        if expected_contract_digest is not None and secure.aad.contract_digest != expected_contract_digest:
            raise ShareViewSecurityError("security_contract_mismatch", status_code=409)
        current_epoch = self._epochs.current_epoch("session", session_id)
        if current_epoch is None or secure.epoch != current_epoch:
            raise ShareViewSecurityError("epoch_mismatch", status_code=409)
        if authorizer is not None:
            authorizer(secure)
        decision = self._epochs.accept_sequence(
            scope_kind="session",
            scope_id=session_id,
            epoch=secure.epoch,
            sender_id=secure.sender_id,
            authenticated_sender_id=authenticated_sender_id,
            traffic_class=secure.aad.traffic_class,
            sequence=secure.sequence,
            nonce_b64=secure.nonce_b64,
        )
        if not decision.accepted:
            raise ShareViewSecurityError(decision.reason_code, status_code=409)
        return secure


# Additive compatibility aliases for the original Pair-View-only public API.
ShareViewSecurityService = ShareSecureEnvelopeService
ShareSecureEnvelopeError = ShareViewSecurityError


__all__ = [
    "ShareSecureEnvelopeError",
    "ShareSecureEnvelopeService",
    "ShareViewEpochPort",
    "ShareViewSecurityError",
    "ShareViewSecurityService",
]
