"""Peer-receipt bridge to Hub curation and the canonical M7 dataset builder."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol

from agent.services.speech_evidence_receipt_service import SpeechEvidenceAdmissionReceipt


@dataclass(frozen=True)
class SpeechContributorManifest:
    contributor_digest: str
    direction: str
    consent_digest: str
    field_provenance_digest: str
    accepted_group_ids: tuple[str, ...]


@dataclass(frozen=True)
class SpeechDatasetVersionReference:
    dataset_id: str
    version: str
    manifest_digest: str
    parent_version: str


@dataclass(frozen=True)
class SpeechDatasetCurationSubmission:
    admission_digest: str
    task_id: str
    duplicate: bool


class HubSpeechCurationTaskPort(Protocol):
    """Adapter over M7 SpeechEvidenceCurationTaskService.create."""

    def enqueue_once(
        self,
        *,
        idempotency_key: str,
        task_type: str,
        payload: Mapping[str, object],
    ) -> tuple[str, bool]: ...


class CanonicalSpeechDatasetPort(Protocol):
    """Adapter over M7 MlInternSpeechDatasetBuildService.build."""

    def build_child_version_once(
        self,
        *,
        idempotency_key: str,
        parent_dataset_id: str,
        parent_version: str,
        receipt: SpeechEvidenceAdmissionReceipt,
        contributors: tuple[SpeechContributorManifest, ...],
    ) -> SpeechDatasetVersionReference: ...


class SpeechEvidencePeerEnrichmentService:
    """Owns neither splitting, training, evaluation nor adapter state."""

    def __init__(
        self,
        *,
        tasks: HubSpeechCurationTaskPort,
        datasets: CanonicalSpeechDatasetPort,
    ) -> None:
        self._tasks = tasks
        self._datasets = datasets

    def submit(
        self,
        receipt: SpeechEvidenceAdmissionReceipt,
        *,
        parent_dataset_id: str,
        parent_version: str,
    ) -> SpeechDatasetCurationSubmission:
        task_id, duplicate = self._tasks.enqueue_once(
            idempotency_key=receipt.admission_digest,
            task_type="speech_peer_evidence_curation",
            payload={
                "admission_digest": receipt.admission_digest,
                "receipt_id": receipt.receipt_id,
                "parent_dataset_id": parent_dataset_id,
                "parent_version": parent_version,
                "accepted_group_count": len(receipt.accepted_group_ids),
            },
        )
        return SpeechDatasetCurationSubmission(receipt.admission_digest, task_id, duplicate)

    def execute_hub_curation(
        self,
        receipt: SpeechEvidenceAdmissionReceipt,
        *,
        parent_dataset_id: str,
        parent_version: str,
        contributors: tuple[SpeechContributorManifest, ...],
    ) -> SpeechDatasetVersionReference:
        accepted = set(receipt.accepted_group_ids)
        manifested = {group_id for row in contributors for group_id in row.accepted_group_ids}
        if accepted != manifested or not contributors:
            raise ValueError("speech_peer_dataset_contributor_manifest_mismatch")
        for row in contributors:
            if row.direction != receipt.direction or row.consent_digest != receipt.consent_digest:
                raise ValueError("speech_peer_dataset_contributor_scope_mismatch")
        result = self._datasets.build_child_version_once(
            idempotency_key=receipt.admission_digest,
            parent_dataset_id=parent_dataset_id,
            parent_version=parent_version,
            receipt=receipt,
            contributors=contributors,
        )
        if result.parent_version != parent_version:
            raise ValueError("speech_peer_dataset_parent_mutation_detected")
        return result


__all__ = [
    "CanonicalSpeechDatasetPort",
    "HubSpeechCurationTaskPort",
    "SpeechContributorManifest",
    "SpeechDatasetCurationSubmission",
    "SpeechDatasetVersionReference",
    "SpeechEvidencePeerEnrichmentService",
]
