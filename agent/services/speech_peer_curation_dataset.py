"""Worker-artifact staging and dataset materialisation for peer curation.

Extracted from ``agent.services.speech_evidence_peer_curation_composition``
(SRP): replay-safe staging of the content-free Worker artifact and building the
canonical dataset record under the current consent fence are persistence-side
steps of result admission, separate from request orchestration.
"""

from __future__ import annotations

from typing import Callable, Mapping

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.speech_evidence import (
    SpeechDatasetManifestDB,
    SpeechEvidenceConsentDB,
    SpeechEvidenceDB,
    SpeechPeerCurationArtifactDB,
    SpeechPeerEvidenceCurationDB,
)
from agent.services.ml_intern_speech_dataset_build_service import MlInternSpeechDatasetBuildService
from agent.services.speech_peer_curation_values import (
    SpeechPeerCurationError,
    canonical_sha256,
    joined_digest,
)
from agent.services.voice_governance_domain import VoicePrincipal
from ananta_contracts.speech_evidence_governance import SpeechCurationWorkerResult


class SpeechPeerCurationArtifactStager:
    """Stage one Worker artifact idempotently; replays must match exactly."""

    def __init__(self, *, clock_ms: Callable[[], int]) -> None:
        self._clock_ms = clock_ms

    def stage(
        self,
        principal: VoicePrincipal,
        result: SpeechCurationWorkerResult,
        artifact: Mapping[str, object],
    ) -> None:
        now = self._clock_ms()
        row = SpeechPeerCurationArtifactDB(
            id=f"speech-peer-artifact-{joined_digest(principal.tenant_id, result.task_id)[:32]}",
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            task_id=result.task_id,
            admission_digest=result.admission_digest,
            artifact_ref=result.artifact_ref,
            artifact_digest=result.artifact_digest,
            artifact_payload=dict(artifact),
            consent_version=result.consent_version,
            revocation_epoch=result.revocation_epoch,
            fencing_token=result.fencing_token,
            created_at_ms=now,
            updated_at_ms=now,
        )
        try:
            with Session(engine) as session:
                session.add(row)
                session.commit()
        except IntegrityError:
            with Session(engine) as session:
                existing = session.exec(
                    select(SpeechPeerCurationArtifactDB).where(
                        SpeechPeerCurationArtifactDB.tenant_id == principal.tenant_id,
                        SpeechPeerCurationArtifactDB.task_id == result.task_id,
                    )
                ).first()
            if existing is None or (
                existing.artifact_ref != result.artifact_ref
                or existing.artifact_digest != result.artifact_digest
                or dict(existing.artifact_payload or {}) != dict(artifact)
            ):
                raise SpeechPeerCurationError("speech_peer_curation_artifact_replay_mismatch")


class SpeechPeerCurationDatasetMaterializer:
    """Build the canonical dataset record for an admitted, still-consented curation."""

    def __init__(
        self,
        *,
        datasets: MlInternSpeechDatasetBuildService,
        clock_ms: Callable[[], int],
    ) -> None:
        self._datasets = datasets
        self._clock_ms = clock_ms

    def build(
        self,
        principal: VoicePrincipal,
        curation: SpeechPeerEvidenceCurationDB,
        result: SpeechCurationWorkerResult,
        artifact: Mapping[str, object],
    ) -> tuple[dict[str, object], bool]:
        with Session(engine) as session:
            evidence = session.exec(
                select(SpeechEvidenceDB).where(
                    SpeechEvidenceDB.id == curation.evidence_id,
                    SpeechEvidenceDB.tenant_id == principal.tenant_id,
                    SpeechEvidenceDB.owner_subject == principal.subject,
                    SpeechEvidenceDB.state == "admitted",
                    SpeechEvidenceDB.admission_digest == result.admission_digest,
                )
            ).first()
            consent = session.exec(
                select(SpeechEvidenceConsentDB).where(
                    SpeechEvidenceConsentDB.id == curation.consent_id,
                    SpeechEvidenceConsentDB.tenant_id == principal.tenant_id,
                    SpeechEvidenceConsentDB.owner_subject == principal.subject,
                    SpeechEvidenceConsentDB.state == "active",
                    SpeechEvidenceConsentDB.consent_version == result.consent_version,
                    SpeechEvidenceConsentDB.revocation_epoch == result.revocation_epoch,
                )
            ).first()
            latest = session.exec(
                select(SpeechDatasetManifestDB)
                .where(
                    SpeechDatasetManifestDB.tenant_id == principal.tenant_id,
                    SpeechDatasetManifestDB.owner_subject == principal.subject,
                    SpeechDatasetManifestDB.dataset_id == curation.dataset_id,
                    SpeechDatasetManifestDB.status == "active",
                )
                .order_by(SpeechDatasetManifestDB.created_at_ms.desc())
            ).first()
        if evidence is None or consent is None or consent.expires_at_ms <= self._clock_ms():
            raise SpeechPeerCurationError("speech_peer_curation_dataset_consent_stale", status_code=403)
        if artifact["evidence_record_digest"] != evidence.content_digest:
            raise SpeechPeerCurationError("speech_peer_curation_artifact_evidence_mismatch", status_code=409)
        contributor = curation.contributor_digest
        source = evidence.source_digest
        report_digest = str(artifact["curation_report_digest"])
        record = {
            "record_digest": evidence.content_digest,
            "lineage_kind": "evidence",
            "source_digest": source,
            "utterance_family_id": evidence.utterance_family_id,
            "session_group_id": canonical_sha256(
                {"pair_id": curation.pair_id, "session_id": curation.session_id, "epoch": curation.session_epoch}
            ),
            "near_duplicate_group_id": canonical_sha256(
                {"source_digest": source, "utterance_family_id": evidence.utterance_family_id}
            ),
            "contributors": [contributor],
            "data_classes": [curation.data_class],
            "field_provenance": {
                "transcript": {"contributor_digest": contributor, "source_digest": source},
            },
            "consent_refs": [
                {
                    "consent_id": consent.id,
                    "consent_version": consent.consent_version,
                    "revocation_epoch": consent.revocation_epoch,
                    "consent_digest": consent.consent_digest,
                }
            ],
            "duration_ms": int(artifact["duration_ms"]),
            "curation_report_digest": report_digest,
        }
        return self._datasets.build(
            principal,
            dataset_id=curation.dataset_id,
            records=(record,),
            curation_report_digest=report_digest,
            parent_digest=latest.manifest_digest if latest is not None else None,
            authority="hub",
        )


__all__ = ["SpeechPeerCurationArtifactStager", "SpeechPeerCurationDatasetMaterializer"]
