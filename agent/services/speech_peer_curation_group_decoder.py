"""Rebind disclosed clear peer-evidence groups to signed transfer commitments.

Extracted from ``agent.services.speech_evidence_peer_curation_composition``
(SRP): decoding and verifying browser-disclosed chunks against the signed
offer previews and acknowledged chunk digests, plus the derived poisoning risk
signal, is pure validation without persistence side effects.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
from typing import Mapping

from agent.repositories.speech_evidence_sync import SpeechEvidenceTransferCurationBinding
from agent.services.speech_evidence_offer_service import (
    SpeechEvidenceOfferRecord,
    speech_evidence_quality_policy_digest,
    speech_evidence_speaker_scope_digest,
)
from agent.services.speech_evidence_poisoning_policy import EvidenceCandidateRiskSignal
from agent.services.speech_peer_curation_values import (
    DIGEST_PATTERN,
    MAX_AGGREGATE_BYTES,
    MAX_GROUP_BYTES,
    SpeechPeerCurationError,
    SpeechPeerCurationGroupInput,
    canonical_sha256,
    parse_group,
)
from ananta_contracts.speech_evidence_sync import (
    group_preview_group_id,
    group_preview_resolution_digest,
)

DecodedPeerGroup = tuple[str, bytes, dict[str, object], str]


class SpeechPeerCurationGroupDecoder:
    """Verify clear groups against signed previews and acknowledged chunks."""

    def decode(
        self,
        supplied: tuple[SpeechPeerCurationGroupInput, ...],
        bindings: tuple[SpeechEvidenceTransferCurationBinding, ...],
        *,
        offer: SpeechEvidenceOfferRecord,
        speaker_id: str,
    ) -> tuple[DecodedPeerGroup, ...]:
        requested = {value.group_id: value for value in supplied}
        decoded: list[DecodedPeerGroup] = []
        aggregate_bytes = 0
        expected_speaker_scope = speech_evidence_speaker_scope_digest(
            pair_id=offer.pair_id,
            epoch=offer.epoch,
            speaker_id=speaker_id,
        )
        expected_quality = speech_evidence_quality_policy_digest()
        authorized_previews = {value.group_id: value for value in offer.group_previews}
        for binding in sorted(bindings, key=lambda value: value.group_id):
            preview = binding.preview
            if (
                binding.offer_id != offer.offer_id
                or binding.offer_group_preview_digest != offer.group_preview_digest
                or authorized_previews.get(binding.group_id) != preview
                or preview.group_id != binding.group_id
                or preview.group_id
                != group_preview_group_id(preview.source_group_digest, preview.revision)
                or preview.resolution_digest
                != group_preview_resolution_digest(preview.source_group_digest, preview.revision)
                or preview.speaker_scope_digest != expected_speaker_scope
                or preview.quality_basis != "policy"
                or preview.quality_digest != expected_quality
                or preview.size_bytes != binding.received_bytes
            ):
                raise SpeechPeerCurationError(
                    "speech_peer_curation_offer_preview_invalid",
                    status_code=409,
                )
            item = requested[binding.group_id]
            if len(item.chunks_b64) != len(binding.chunks):
                raise SpeechPeerCurationError("speech_peer_curation_chunk_binding_mismatch", status_code=409)
            chunks: list[bytes] = []
            for encoded, expected in zip(item.chunks_b64, binding.chunks, strict=True):
                try:
                    chunk = base64.b64decode(encoded, validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise SpeechPeerCurationError(
                        "speech_peer_curation_chunk_encoding_invalid", status_code=422
                    ) from exc
                if len(chunk) != expected.plaintext_bytes or not hmac.compare_digest(
                    hashlib.sha256(chunk).hexdigest(), expected.plaintext_digest
                ):
                    raise SpeechPeerCurationError("speech_peer_curation_chunk_digest_mismatch", status_code=409)
                chunks.append(chunk)
            body = b"".join(chunks)
            if not body or len(body) != binding.received_bytes or len(body) > MAX_GROUP_BYTES:
                raise SpeechPeerCurationError("speech_peer_curation_group_size_invalid", status_code=413)
            payload = parse_group(body)
            content_digest = hashlib.sha256(body).hexdigest()
            if payload["source_digest"] != preview.source_group_digest:
                raise SpeechPeerCurationError(
                    "speech_peer_curation_source_group_mismatch",
                    status_code=409,
                )
            if payload["revision"] != preview.revision:
                raise SpeechPeerCurationError(
                    "speech_peer_curation_revision_mismatch",
                    status_code=409,
                )
            aggregate_bytes += len(body)
            if aggregate_bytes > MAX_AGGREGATE_BYTES:
                raise SpeechPeerCurationError("speech_peer_curation_payload_too_large", status_code=413)
            decoded.append((binding.group_id, body, payload, content_digest))
        return tuple(decoded)

    @staticmethod
    def risk_signal(
        *,
        group_id: str,
        payload: Mapping[str, object],
        content_digest: str,
        contributor_digest: str,
        consent_digest: str,
    ) -> EvidenceCandidateRiskSignal:
        candidates = list(payload["candidates"])
        authorities = [str(dict(value)["authority"]) for value in candidates]
        text = "\n".join(str(dict(value)["text"]) for value in candidates).lower()
        return EvidenceCandidateRiskSignal(
            candidate_id=group_id,
            source_role="speaker",
            contributor_digest=contributor_digest,
            lineage_digest=str(payload["source_digest"]),
            model_digest=canonical_sha256(authorities),
            validator_set_digest=consent_digest,
            signature_valid=True,
            digest_valid=DIGEST_PATTERN.fullmatch(content_digest) is not None,
            speaker_scope_valid=True,
            replayed=False,
            confidence_micros=750_000,
            contradictory_revision_count=0,
            distribution_distance_micros=0,
            trigger_phrase_detected="targeted trigger" in text,
        )


__all__ = ["DecodedPeerGroup", "SpeechPeerCurationGroupDecoder"]
