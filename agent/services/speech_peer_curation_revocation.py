"""Transitive revocation fence for one peer speech-evidence curation.

Extracted from ``agent.services.speech_evidence_peer_curation_composition``
(SRP): invalidating a curation, its delegated task, the admitted evidence, the
content key and every lineage descendant is one ordered revocation workflow.
Each authority mutation stages its content-free audit command in the same Hub
transaction through the closed audit outbox, exactly as before the split.
"""

from __future__ import annotations

import hashlib
from typing import Callable

from sqlalchemy import update
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.speech_evidence import (
    SpeechDatasetManifestDB,
    SpeechPeerCurationArtifactDB,
    SpeechPeerEvidenceCurationDB,
)
from agent.repositories.semantic_media_audit_outbox import SqlSemanticMediaAuditOutbox
from agent.repositories.speech_evidence import SpeechEvidenceRepository
from agent.repositories.speech_evidence_lineage import SpeechEvidenceLineageRepository
from agent.services.ml_intern_speech_lineage_service import MlInternSpeechLineageService
from agent.services.ml_intern_speech_revocation_service import MlInternSpeechRevocationService
from agent.services.speech_evidence_curation_task_service import SpeechEvidenceCurationTaskService
from agent.services.speech_evidence_encryption_port import SpeechEvidenceEncryptionPort
from agent.services.speech_peer_curation_adapters import SpeechPeerCurationAuditPreparer
from agent.services.speech_peer_curation_values import SpeechPeerCurationError
from agent.services.voice_governance_domain import VoicePrincipal


class SpeechPeerCurationRevocationFence:
    """Fence one curation and all of its local descendants in a fixed order."""

    def __init__(
        self,
        *,
        curation_tasks: SpeechEvidenceCurationTaskService,
        evidence: SpeechEvidenceRepository,
        encryption: SpeechEvidenceEncryptionPort,
        lineage: MlInternSpeechLineageService,
        lineage_repository: SpeechEvidenceLineageRepository,
        training_revocation: MlInternSpeechRevocationService,
        audit: SpeechPeerCurationAuditPreparer,
        clock_ms: Callable[[], int],
    ) -> None:
        self._tasks = curation_tasks
        self._evidence = evidence
        self._encryption = encryption
        self._lineage_service = lineage
        self._lineage_repository = lineage_repository
        self._training_revocation = training_revocation
        self._audit = audit
        self._clock_ms = clock_ms

    def fence(self, *, curation_id: str, reason_code: str) -> None:
        now = self._clock_ms()
        with Session(engine) as session:
            row = session.exec(
                select(SpeechPeerEvidenceCurationDB)
                .where(SpeechPeerEvidenceCurationDB.id == curation_id)
                .with_for_update()
            ).first()
            if row is None:
                return
            if row.state == "invalidated":
                return
            owner = VoicePrincipal(row.tenant_id, row.owner_subject)
            reason_digest = hashlib.sha256(reason_code.encode()).hexdigest()
            started_audit = self._audit.prepare(
                owner,
                session_id=row.session_id,
                epoch=row.session_epoch,
                transition="curation_revocation_started",
                reason_code="speech_curation_revocation_started",
                idempotency_key=f"speech-curation:revoke-start:{row.id}:{reason_digest}",
                authority_ref=f"{row.id}:{reason_digest}",
            )
            row.state = "invalidating"
            row.updated_at_ms = now
            session.add(row)
            if started_audit is not None:
                SqlSemanticMediaAuditOutbox.enqueue_in_session(session, started_audit)
            session.commit()
            evidence_id = row.evidence_id
            task_id = row.curation_task_id
            fence_epoch = row.revocation_epoch + 1
            session_id = row.session_id
            session_epoch = row.session_epoch
        if task_id is not None:
            self._tasks.fence(owner, task_id, reason_code=reason_code)
        evidence = self._evidence.get(
            tenant_id=owner.tenant_id,
            owner_subject=owner.subject,
            evidence_id=evidence_id,
        )
        if evidence is None:
            raise SpeechPeerCurationError("speech_peer_curation_evidence_missing", status_code=503)
        evidence_audit = self._audit.prepare(
            owner,
            session_id=session_id,
            epoch=session_epoch,
            transition="curation_evidence_revoked",
            reason_code="speech_curation_evidence_revoked",
            idempotency_key=f"speech-curation:evidence-revoked:{curation_id}:{reason_digest}",
            authority_ref=f"{evidence.evidence_id}:{reason_digest}",
        )
        self._evidence.transition(
            tenant_id=owner.tenant_id,
            owner_subject=owner.subject,
            evidence_id=evidence.evidence_id,
            expected_states=("quarantined", "admitted", "rejected", "accepted", "revoked"),
            target="revoked",
            now_ms=now,
            admission_digest=evidence.admission_digest,
            audit_event=evidence_audit,
        )
        self._encryption.destroy(evidence.key_id, tenant_id=owner.tenant_id)
        try:
            impact = self._lineage_service.impact(
                owner,
                root_kind="evidence",
                root_digest=evidence.content_digest,
                revocation_epoch=fence_epoch,
            )
        except Exception as exc:
            raise SpeechPeerCurationError(
                str(getattr(exc, "reason_code", "speech_peer_curation_lineage_unavailable")),
                status_code=503,
            ) from exc
        self._training_revocation.fence_impact(owner, impact)
        manifest_digests = tuple(
            str(node["digest"]) for node in impact.nodes if str(node["kind"]) == "manifest"
        )
        completed_audit = self._audit.prepare(
            owner,
            session_id=session_id,
            epoch=session_epoch,
            transition="curation_revoked",
            reason_code="speech_curation_revoked",
            idempotency_key=f"speech-curation:revoked:{curation_id}:{reason_digest}",
            authority_ref=f"{curation_id}:{reason_digest}",
        )
        with Session(engine) as session:
            if manifest_digests:
                session.exec(
                    update(SpeechDatasetManifestDB)
                    .where(
                        SpeechDatasetManifestDB.tenant_id == owner.tenant_id,
                        SpeechDatasetManifestDB.owner_subject == owner.subject,
                        SpeechDatasetManifestDB.manifest_digest.in_(manifest_digests),
                        SpeechDatasetManifestDB.status == "active",
                    )
                    .values(status="revoked")
                )
            if task_id is not None:
                session.exec(
                    update(SpeechPeerCurationArtifactDB)
                    .where(
                        SpeechPeerCurationArtifactDB.tenant_id == owner.tenant_id,
                        SpeechPeerCurationArtifactDB.owner_subject == owner.subject,
                        SpeechPeerCurationArtifactDB.task_id == task_id,
                        SpeechPeerCurationArtifactDB.state.in_(["quarantined", "published"]),
                    )
                    .values(state="fenced", updated_at_ms=now)
                )
            session.exec(
                update(SpeechPeerEvidenceCurationDB)
                .where(SpeechPeerEvidenceCurationDB.id == curation_id)
                .values(state="invalidated", updated_at_ms=now)
            )
            if completed_audit is not None:
                SqlSemanticMediaAuditOutbox.enqueue_in_session(session, completed_audit)
            session.commit()
        self._lineage_repository.mark_status(
            tenant_id=owner.tenant_id,
            owner_subject=owner.subject,
            nodes=((str(node["kind"]), str(node["digest"])) for node in impact.nodes),
            status="revoked",
            revocation_epoch=fence_epoch,
            now_ms=now,
        )


__all__ = ["SpeechPeerCurationRevocationFence"]
