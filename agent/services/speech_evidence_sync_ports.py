"""Relay ports used by the Hub speech-evidence sync service.

Extracted from ``agent.services.speech_evidence_sync_composition`` so the sync
service depends on narrow relay abstractions (ISP/DIP) rather than on the
concrete semantic relay implementation.
"""

from __future__ import annotations

from typing import Protocol

from ananta_contracts.webrtc_datachannel import ValidatedDataChannelMessage


class SpeechEvidenceOpaqueRelayPort(Protocol):
    def append_ciphertext(
        self,
        *,
        tenant_id: str,
        authenticated_sender_id: str,
        offer_id: str,
        message: ValidatedDataChannelMessage,
    ) -> dict: ...

    def acknowledge_bytes(self, offer_id: str, sender_id: str, audience_id: str, count: int) -> None: ...

    def revoke_ciphertext(
        self,
        *,
        tenant_id: str,
        session_id: str,
        epoch: int,
        offer_id: str,
        message_ids: tuple[str, ...],
    ) -> int: ...


class SpeechEvidenceControlRelayPort(Protocol):
    def append_message(
        self,
        *,
        tenant_id: str,
        authenticated_sender_id: str,
        message: ValidatedDataChannelMessage,
    ) -> dict: ...


__all__ = [
    "SpeechEvidenceControlRelayPort",
    "SpeechEvidenceOpaqueRelayPort",
]
