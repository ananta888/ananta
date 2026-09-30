"""Lossless mapping between speech-evidence Offer records and their SQL rows."""

from __future__ import annotations

from typing import Any

from agent.db_models.speech_evidence_sync import SpeechEvidenceOfferDB
from agent.models.speech_evidence_offer import SpeechEvidenceGroupPreview, SpeechEvidenceOfferRecord


def offer_row(record: SpeechEvidenceOfferRecord, *, now_ms: int) -> SpeechEvidenceOfferDB:
    return SpeechEvidenceOfferDB(**offer_values(record), created_at_ms=now_ms, updated_at_ms=now_ms)


def offer_values(record: SpeechEvidenceOfferRecord) -> dict[str, Any]:
    return {
        "offer_id": record.offer_id,
        "tenant_id": record.tenant_id,
        "proposal_verification_digest": record.proposal_verification_digest,
        "acceptance_verification_digest": record.acceptance_verification_digest,
        "session_id": record.session_id,
        "pair_id": record.pair_id,
        "epoch": record.epoch,
        "sender_id": record.sender_id,
        "recipient_id": record.recipient_id,
        "inventory_root_digest": record.inventory_root_digest,
        "direction": record.direction,
        "purpose": record.purpose,
        "data_classes": list(record.data_classes),
        "fields": list(record.fields),
        "retention_seconds": record.retention_seconds,
        "trainer_class": record.trainer_class,
        "group_ids": list(record.group_ids),
        "group_previews": [row.public_dict() for row in record.group_previews],
        "group_preview_digest": record.group_preview_digest,
        "total_bytes": record.total_bytes,
        "sender_consent_digest": record.sender_consent_digest,
        "recipient_consent_digest": record.recipient_consent_digest,
        "scope_digest": record.scope_digest,
        "expires_at_ms": record.expires_at_ms,
        "state": record.state,
        "transfer_started": record.transfer_started,
        "invalidation_reason": record.invalidation_reason,
        "protocol_version": record.protocol_version,
        "version": record.version,
    }


def offer_record(row: SpeechEvidenceOfferDB) -> SpeechEvidenceOfferRecord:
    return SpeechEvidenceOfferRecord(
        offer_id=row.offer_id,
        proposal_verification_digest=row.proposal_verification_digest,
        acceptance_verification_digest=row.acceptance_verification_digest,
        session_id=row.session_id,
        pair_id=row.pair_id,
        epoch=int(row.epoch),
        sender_id=row.sender_id,
        recipient_id=row.recipient_id,
        inventory_root_digest=row.inventory_root_digest,
        direction=row.direction,
        purpose=row.purpose,
        data_classes=tuple(str(value) for value in row.data_classes),
        fields=tuple(str(value) for value in row.fields),
        retention_seconds=int(row.retention_seconds),
        trainer_class=row.trainer_class,
        group_ids=tuple(str(value) for value in row.group_ids),
        group_previews=tuple(
            SpeechEvidenceGroupPreview.from_mapping(value)
            for value in (row.group_previews or [])
        ),
        group_preview_digest=str(row.group_preview_digest or ""),
        total_bytes=int(row.total_bytes),
        sender_consent_digest=row.sender_consent_digest,
        recipient_consent_digest=row.recipient_consent_digest,
        scope_digest=row.scope_digest,
        expires_at_ms=int(row.expires_at_ms),
        state=row.state,
        transfer_started=bool(row.transfer_started),
        invalidation_reason=row.invalidation_reason,
        tenant_id=row.tenant_id,
        version=int(row.version),
        protocol_version=str(row.protocol_version or "ananta.speech-evidence-sync.v1"),
    )


__all__ = ["offer_record", "offer_row", "offer_values"]
