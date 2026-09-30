"""Content-free speech-evidence offer records and group preview bindings.

These dependency-free value objects are shared by the Hub offer service and
the SQL offer repository. They live in the model layer so the persistence
adapter does not depend on the service layer; the service module re-exports
them unchanged.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping

from ananta_contracts.speech_evidence_sync import canonical_sha256


class SpeechEvidenceOfferError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int = 422) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class SpeechEvidenceCandidateProjection:
    ordinal: int
    candidate_digest: str
    authority_digest: str
    revision: int

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SpeechEvidenceCandidateProjection:
        return cls(
            ordinal=int(value["ordinal"]),
            candidate_digest=str(value["candidate_digest"]),
            authority_digest=str(value["authority_digest"]),
            revision=int(value["revision"]),
        )

    def public_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class SpeechEvidenceGroupPreview:
    """Content-free, signed pre-acceptance binding for one evidence group."""

    preview_version: str
    group_id: str
    source_group_digest: str
    speaker_scope_digest: str
    quality_basis: str
    quality_digest: str
    resolution_digest: str
    original_candidates: tuple[SpeechEvidenceCandidateProjection, ...]
    resolution_state: str
    selected_candidate_digest: str | None
    unresolved_region_digests: tuple[str, ...]
    comparison_digest: str
    revision: int
    size_bytes: int

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SpeechEvidenceGroupPreview:
        return cls(
            preview_version=str(value["preview_version"]),
            group_id=str(value["group_id"]),
            source_group_digest=str(value["source_group_digest"]),
            speaker_scope_digest=str(value["speaker_scope_digest"]),
            quality_basis=str(value["quality_basis"]),
            quality_digest=str(value["quality_digest"]),
            resolution_digest=str(value["resolution_digest"]),
            original_candidates=tuple(
                SpeechEvidenceCandidateProjection.from_mapping(candidate)
                for candidate in value["original_candidates"]  # type: ignore[union-attr]
                if isinstance(candidate, Mapping)
            ),
            resolution_state=str(value["resolution_state"]),
            selected_candidate_digest=(
                str(value["selected_candidate_digest"])
                if value["selected_candidate_digest"] is not None
                else None
            ),
            unresolved_region_digests=tuple(str(item) for item in value["unresolved_region_digests"]),  # type: ignore[union-attr]
            comparison_digest=str(value["comparison_digest"]),
            revision=int(value["revision"]),
            size_bytes=int(value["size_bytes"]),
        )

    def public_dict(self) -> dict[str, object]:
        return {
            "preview_version": self.preview_version,
            "group_id": self.group_id,
            "source_group_digest": self.source_group_digest,
            "speaker_scope_digest": self.speaker_scope_digest,
            "quality_basis": self.quality_basis,
            "quality_digest": self.quality_digest,
            "resolution_digest": self.resolution_digest,
            "original_candidates": [value.public_dict() for value in self.original_candidates],
            "resolution_state": self.resolution_state,
            "selected_candidate_digest": self.selected_candidate_digest,
            "unresolved_region_digests": list(self.unresolved_region_digests),
            "comparison_digest": self.comparison_digest,
            "revision": self.revision,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class SpeechEvidenceOfferRecord:
    offer_id: str
    proposal_verification_digest: str
    acceptance_verification_digest: str | None
    session_id: str
    pair_id: str
    epoch: int
    sender_id: str
    recipient_id: str
    inventory_root_digest: str
    direction: str
    purpose: str
    data_classes: tuple[str, ...]
    fields: tuple[str, ...]
    retention_seconds: int
    trainer_class: str
    group_ids: tuple[str, ...]
    total_bytes: int
    sender_consent_digest: str
    recipient_consent_digest: str
    scope_digest: str
    expires_at_ms: int
    state: str
    group_previews: tuple[SpeechEvidenceGroupPreview, ...] = ()
    group_preview_digest: str = ""
    transfer_started: bool = False
    invalidation_reason: str | None = None
    tenant_id: str = ""
    version: int = 1
    protocol_version: str = "ananta.speech-evidence-sync.v1"


def group_preview_digest(previews: tuple[SpeechEvidenceGroupPreview, ...]) -> str:
    return canonical_sha256(
        {
            "domain": "ananta.speech-evidence-group-preview-set.v1",
            "groups": [row.public_dict() for row in sorted(previews, key=lambda item: item.group_id)],
        }
    )


__all__ = [
    "SpeechEvidenceCandidateProjection",
    "SpeechEvidenceGroupPreview",
    "SpeechEvidenceOfferError",
    "SpeechEvidenceOfferRecord",
    "group_preview_digest",
]
