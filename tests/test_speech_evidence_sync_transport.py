from __future__ import annotations

import pytest

from agent.services.semantic_speech_relay import (
    SemanticSpeechRelay,
    SemanticSpeechRelayError,
    SpeechRelayLimits,
)
from agent.services.speech_evidence_offer_service import SpeechEvidenceOfferRecord
from ananta_contracts.webrtc_datachannel import ValidatedDataChannelMessage
from tests.speech_evidence_sync_support import digest


class _Relay:
    def __init__(self) -> None:
        self.rows = []

    def append_message(self, **values):
        self.rows.append(values)
        return {"message_id": values["message"].message_id}


class _Offers:
    def authorize_transfer(self, offer_id):
        return SpeechEvidenceOfferRecord(
            offer_id=offer_id,
            proposal_verification_digest=digest("proposal"),
            acceptance_verification_digest=digest("acceptance"),
            session_id="session-test",
            pair_id="pair-test",
            epoch=7,
            sender_id="peer-a",
            recipient_id="peer-b",
            inventory_root_digest=digest("inventory"),
            direction="sender_to_receiver",
            purpose="speech_dataset_curation",
            data_classes=("text_corrections",),
            fields=("transcript",),
            retention_seconds=3600,
            trainer_class="speech_adaptation",
            group_ids=("group-a",),
            total_bytes=1024,
            sender_consent_digest=digest("sender-consent"),
            recipient_consent_digest=digest("recipient-consent"),
            scope_digest=digest("scope"),
            expires_at_ms=2_000_000,
            state="accepted",
            transfer_started=True,
        )


def _message(*, ciphertext=b"opaque-ciphertext", sender="peer-a", audience="peer-b"):
    return ValidatedDataChannelMessage(
        version="ananta.webrtc-datachannel.v1",
        traffic_class="evidence_bulk",
        message_id=digest(ciphertext.hex()),
        session_id="session-test",
        epoch=7,
        sender_id=sender,
        audience_id=audience,
        sequence=1,
        expires_at_ms=2_000_000,
        compression="none",
        security={"mode": "aead"},
        ciphertext=ciphertext,
        payload_digest=digest("payload"),
    )


def test_relay_sees_only_opaque_ciphertext_and_enforces_offer_binding() -> None:
    relay = _Relay()
    service = SemanticSpeechRelay(relay=relay, offers=_Offers())
    result = service.append_ciphertext(
        tenant_id="tenant-test",
        authenticated_sender_id="peer-a",
        offer_id="offer-test",
        message=_message(),
    )
    assert result["message_id"]
    assert relay.rows[0]["message"].ciphertext == b"opaque-ciphertext"
    assert not hasattr(service, "decrypt") and not hasattr(service, "key")

    with pytest.raises(SemanticSpeechRelayError) as captured:
        service.append_ciphertext(
            tenant_id="tenant-test",
            authenticated_sender_id="peer-a",
            offer_id="offer-test",
            message=_message(audience="peer-foreign"),
        )
    assert captured.value.reason_code == "speech_relay_offer_binding_mismatch"


def test_relay_applies_chunk_and_inflight_limits_before_store() -> None:
    relay = _Relay()
    service = SemanticSpeechRelay(
        relay=relay,
        offers=_Offers(),
        limits=SpeechRelayLimits(maximum_chunk_bytes=64, maximum_in_flight_bytes=64),
    )
    service.append_ciphertext(
        tenant_id="tenant-test",
        authenticated_sender_id="peer-a",
        offer_id="offer-test",
        message=_message(ciphertext=b"x" * 40),
    )
    with pytest.raises(SemanticSpeechRelayError) as captured:
        service.append_ciphertext(
            tenant_id="tenant-test",
            authenticated_sender_id="peer-a",
            offer_id="offer-test",
            message=_message(ciphertext=b"y" * 40),
        )
    assert captured.value.reason_code == "speech_relay_backpressure"
    assert len(relay.rows) == 1
    service.acknowledge_bytes("offer-test", "peer-a", "peer-b", 40)
    assert service.in_flight_bytes("offer-test", "peer-a", "peer-b") == 0

    with pytest.raises(SemanticSpeechRelayError) as captured:
        service.append_ciphertext(
            tenant_id="tenant-test",
            authenticated_sender_id="peer-a",
            offer_id="offer-test",
            message=_message(ciphertext=b"z" * 65),
        )
    assert captured.value.reason_code == "speech_relay_chunk_oversized"
