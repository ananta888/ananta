"""Opaque evidence-bulk relay policy layered on the canonical semantic relay."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Protocol

from agent.services.semantic_relay_service import SemanticRelayService
from ananta_contracts.speech_evidence_sync import MAX_CHUNK_CIPHERTEXT_BYTES, MAX_MESSAGE_BYTES
from ananta_contracts.webrtc_datachannel import ValidatedDataChannelMessage


class SemanticSpeechRelayError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int = 422) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


class SpeechRelayOfferAuthorizationPort(Protocol):
    def authorize_transfer(self, offer_id: str): ...


@dataclass(frozen=True)
class SpeechRelayLimits:
    maximum_chunk_bytes: int = MAX_CHUNK_CIPHERTEXT_BYTES
    maximum_in_flight_bytes: int = 1024 * 1024


MAX_OPAQUE_EVIDENCE_ENVELOPE_BYTES = MAX_MESSAGE_BYTES * 2


class SemanticSpeechRelay:
    """Relays ciphertext only and never receives AEAD keys or plaintext."""

    def __init__(
        self,
        *,
        relay: SemanticRelayService,
        offers: SpeechRelayOfferAuthorizationPort,
        limits: SpeechRelayLimits = SpeechRelayLimits(),
        enforce_local_in_flight: bool = True,
    ) -> None:
        if not 1 <= limits.maximum_chunk_bytes <= MAX_OPAQUE_EVIDENCE_ENVELOPE_BYTES:
            raise ValueError("speech_relay_chunk_policy_invalid")
        if not limits.maximum_chunk_bytes <= limits.maximum_in_flight_bytes <= 1024 * 1024:
            raise ValueError("speech_relay_inflight_policy_invalid")
        self._relay = relay
        self._offers = offers
        self._limits = limits
        self._enforce_local_in_flight = enforce_local_in_flight
        self._in_flight: dict[tuple[str, str, str], int] = {}
        self._lock = threading.RLock()

    def append_ciphertext(
        self,
        *,
        tenant_id: str,
        authenticated_sender_id: str,
        offer_id: str,
        message: ValidatedDataChannelMessage,
    ) -> dict:
        if message.traffic_class != "evidence_bulk":
            raise SemanticSpeechRelayError("speech_relay_traffic_class_invalid")
        if len(message.ciphertext) > self._limits.maximum_chunk_bytes:
            raise SemanticSpeechRelayError("speech_relay_chunk_oversized", status_code=413)
        offer = self._offers.authorize_transfer(offer_id)
        expected_sender, expected_recipient = (
            (offer.sender_id, offer.recipient_id)
            if offer.direction == "sender_to_receiver"
            else (offer.recipient_id, offer.sender_id)
        )
        if (
            expected_sender != authenticated_sender_id
            or expected_recipient != message.audience_id
            or offer.session_id != message.session_id
            or offer.epoch != message.epoch
        ):
            raise SemanticSpeechRelayError("speech_relay_offer_binding_mismatch", status_code=403)
        key = (offer_id, expected_sender, expected_recipient)
        if self._enforce_local_in_flight:
            with self._lock:
                current = self._in_flight.get(key, 0)
                if current + len(message.ciphertext) > self._limits.maximum_in_flight_bytes:
                    raise SemanticSpeechRelayError("speech_relay_backpressure", status_code=429)
                self._in_flight[key] = current + len(message.ciphertext)
        try:
            return self._relay.append_message(
                tenant_id=tenant_id,
                authenticated_sender_id=authenticated_sender_id,
                message=message,
            )
        except Exception:
            self.acknowledge_bytes(offer_id, authenticated_sender_id, message.audience_id, len(message.ciphertext))
            raise

    def acknowledge_bytes(self, offer_id: str, sender_id: str, audience_id: str, count: int) -> None:
        if not self._enforce_local_in_flight:
            return
        key = (offer_id, sender_id, audience_id)
        with self._lock:
            current = self._in_flight.get(key, 0)
            remaining = max(0, current - max(0, count))
            if remaining:
                self._in_flight[key] = remaining
            else:
                self._in_flight.pop(key, None)

    def in_flight_bytes(self, offer_id: str, sender_id: str, audience_id: str) -> int:
        if not self._enforce_local_in_flight:
            return 0
        with self._lock:
            return self._in_flight.get((offer_id, sender_id, audience_id), 0)

    def revoke_ciphertext(
        self,
        *,
        tenant_id: str,
        session_id: str,
        epoch: int,
        offer_id: str,
        message_ids: tuple[str, ...],
    ) -> int:
        """Remove queued opaque chunks after Hub-side offer invalidation.

        The relay never receives an evidence payload or decryption key.  The
        durable transfer repository supplies only the already-bound relay
        message identifiers.
        """

        removed = 0
        for message_id in message_ids:
            removed += self._relay.revoke(
                tenant_id=tenant_id,
                session_id=session_id,
                message_id=message_id,
                epoch=epoch,
            )
        with self._lock:
            for key in tuple(self._in_flight):
                if key[0] == offer_id:
                    self._in_flight.pop(key, None)
        return removed


__all__ = ["SemanticSpeechRelay", "SemanticSpeechRelayError", "SpeechRelayLimits"]
