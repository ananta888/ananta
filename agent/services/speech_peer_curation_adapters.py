"""Port adapters and read projections for peer speech-evidence curation.

Extracted from ``agent.services.speech_evidence_peer_curation_composition``
(SRP/DIP): the Hub admission MAC, the local dataset publication check, the
staged Worker-result authorization, the content-free audit preparation and the
curation read projection are narrow collaborators injected into the curation
service instead of living inside the application service itself.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Mapping

from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.speech_evidence import (
    SpeechPeerCurationArtifactDB,
    SpeechPeerEvidenceCurationDB,
)
from agent.services.ml_intern_speech_dataset_build_service import MlInternSpeechDatasetBuildService
from agent.services.ml_intern_speech_dataset_port import MlInternSpeechDatasetPort
from agent.services.semantic_media_audit_service import (
    SemanticMediaAuditEvent,
    SemanticMediaAuditPort,
)
from agent.services.speech_evidence_admission_policy import SpeechEvidenceAuthorityPort
from agent.services.speech_evidence_curation_task_service import SpeechCurationResultPort
from agent.services.speech_peer_curation_values import (
    DIGEST_PATTERN,
    SpeechPeerCurationError,
    SpeechPeerCurationRecord,
    require_identifier,
)
from agent.services.voice_governance_domain import VoicePrincipal
from ananta_contracts.speech_evidence_governance import (
    SpeechCurationWorkerResult,
    canonical_json,
)


class HubPeerAdmissionAuthority(SpeechEvidenceAuthorityPort):
    """One-domain Hub MAC used only after sync authority was revalidated."""

    def __init__(self, key: bytes) -> None:
        if len(key) < 32:
            raise ValueError("speech_peer_admission_key_invalid")
        self._key = bytes(key)

    def sign(self, evidence_digest: str) -> str:
        return hmac.new(self._key, evidence_digest.encode("ascii"), hashlib.sha256).hexdigest()

    def authorize(self, **bindings: object) -> tuple[bool, str]:
        digest = str(bindings.get("evidence_digest") or "")
        signature = str(bindings.get("signature") or "")
        if DIGEST_PATTERN.fullmatch(digest) is None or not hmac.compare_digest(self.sign(digest), signature):
            return False, "speech_evidence_hub_admission_signature_invalid"
        return True, "speech_evidence_hub_admission_authorized"


class HubLocalSpeechDatasetPublisher(MlInternSpeechDatasetPort):
    """Fail-closed local publication boundary for content-free manifests.

    The canonical builder writes the manifest and lineage in the same Hub
    transaction after this bounded validation callback.  No filesystem or
    Worker-controlled location is involved.
    """

    def publish_manifest(self, *, tenant_id: str, owner_subject: str, manifest: Mapping[str, object]) -> bool:
        del tenant_id, owner_subject
        if manifest.get("schema") != MlInternSpeechDatasetBuildService.SCHEMA:
            return False
        encoded = canonical_json(dict(manifest))
        return 0 < len(encoded) <= 4 * 1024 * 1024 and not any(
            token in encoded.lower() for token in (b"plaintext", b"private_key", b"file://")
        )


class StagedSpeechCurationResultPort(SpeechCurationResultPort):
    """Authorize only the exact content-free artifact staged by the Worker."""

    def publish(self, result: SpeechCurationWorkerResult) -> bool:
        with Session(engine) as session:
            row = session.exec(
                select(SpeechPeerCurationArtifactDB).where(
                    SpeechPeerCurationArtifactDB.task_id == result.task_id,
                    SpeechPeerCurationArtifactDB.admission_digest == result.admission_digest,
                    SpeechPeerCurationArtifactDB.artifact_ref == result.artifact_ref,
                    SpeechPeerCurationArtifactDB.artifact_digest == result.artifact_digest,
                    SpeechPeerCurationArtifactDB.consent_version == result.consent_version,
                    SpeechPeerCurationArtifactDB.revocation_epoch == result.revocation_epoch,
                    SpeechPeerCurationArtifactDB.fencing_token == result.fencing_token,
                    SpeechPeerCurationArtifactDB.state.in_(["quarantined", "published"]),
                )
            ).first()
            return row is not None


class SpeechPeerCurationAuditPreparer:
    """Prepare content-free curation audit events before an authority mutation."""

    def __init__(self, audit: SemanticMediaAuditPort | None) -> None:
        self._audit = audit

    def prepare(
        self,
        principal: VoicePrincipal,
        *,
        session_id: str,
        epoch: int,
        transition: str,
        reason_code: str,
        idempotency_key: str,
        authority_ref: str,
    ) -> SemanticMediaAuditEvent | None:
        if self._audit is None:
            return None
        try:
            return self._audit.prepare_transition(
                idempotency_key=idempotency_key,
                tenant_id=principal.tenant_id,
                scope=f"speech-evidence:{session_id}",
                event_type="speech_evidence",
                transition=transition,
                reason_code=reason_code,
                epoch=epoch,
                job_ref=authority_ref,
            )
        except Exception as exc:
            raise SpeechPeerCurationError(
                "speech_peer_curation_audit_unavailable",
                status_code=503,
            ) from exc


class SqlSpeechPeerCurationProjection:
    """Read the durable curation projection scoped to its owner or task."""

    def get(self, principal: VoicePrincipal, offer_id: str) -> SpeechPeerCurationRecord | None:
        with Session(engine) as session:
            row = session.exec(
                select(SpeechPeerEvidenceCurationDB).where(
                    SpeechPeerEvidenceCurationDB.tenant_id == principal.tenant_id,
                    SpeechPeerEvidenceCurationDB.owner_subject == principal.subject,
                    SpeechPeerEvidenceCurationDB.offer_id == offer_id,
                )
            ).first()
            return curation_record(row) if row is not None else None

    @staticmethod
    def for_task(task_id: str) -> tuple[SpeechPeerEvidenceCurationDB, VoicePrincipal]:
        task_id = require_identifier(task_id, "speech_peer_curation_task_invalid")
        with Session(engine) as session:
            row = session.exec(
                select(SpeechPeerEvidenceCurationDB).where(SpeechPeerEvidenceCurationDB.curation_task_id == task_id)
            ).first()
            if row is None:
                raise SpeechPeerCurationError("speech_peer_curation_task_not_found", status_code=404)
            session.expunge(row)
        return row, VoicePrincipal(row.tenant_id, row.owner_subject)


def curation_record(row: SpeechPeerEvidenceCurationDB) -> SpeechPeerCurationRecord:
    return SpeechPeerCurationRecord(
        curation_id=row.id,
        offer_id=row.offer_id,
        admission_digest=row.admission_digest,
        state=row.state,
        receipt=dict(row.receipt_payload or {}),
        curation_task_id=row.curation_task_id,
        dataset_id=row.dataset_id,
        dataset_parent_digest=row.dataset_parent_digest,
        dataset_manifest_digest=row.dataset_manifest_digest,
        consent_version=row.consent_version,
        revocation_epoch=row.revocation_epoch,
    )


__all__ = [
    "HubLocalSpeechDatasetPublisher",
    "HubPeerAdmissionAuthority",
    "SpeechPeerCurationAuditPreparer",
    "SqlSpeechPeerCurationProjection",
    "StagedSpeechCurationResultPort",
    "curation_record",
]
