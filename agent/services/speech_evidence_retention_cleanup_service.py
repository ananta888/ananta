"""Crash-resumable bounded cleanup for expired speech evidence."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Protocol

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.speech_evidence import SpeechEvidenceCleanupDB
from agent.repositories.semantic_media_audit_outbox import SqlSemanticMediaAuditOutbox
from agent.repositories.speech_evidence import SpeechEvidenceRepository, get_speech_evidence_repository
from agent.services.ml_intern_speech_lineage_service import (
    MlInternSpeechLineageService,
    get_ml_intern_speech_lineage_service,
)
from agent.services.semantic_media_audit_service import SemanticMediaAuditPort
from agent.services.speech_evidence_encryption_port import (
    SpeechEvidenceEncryptionPort,
    get_speech_evidence_encryption_port,
)
from agent.services.voice_governance_domain import VoicePrincipal


class SpeechTemporaryArtifactCleanupPort(Protocol):
    def cleanup(self, *, tenant_id: str, evidence_id: str) -> bool: ...


class DatabaseOnlySpeechArtifactCleanup:
    """Production adapter for the DB-only quarantine (there is no temp path)."""

    def cleanup(self, *, tenant_id: str, evidence_id: str) -> bool:
        return bool(tenant_id and evidence_id)


class SpeechEvidenceCleanupError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class SpeechEvidenceCleanupSummary:
    staged: int
    completed: int
    pending: int
    skipped_active_references: int
    failed: int


class SpeechEvidenceRetentionCleanupService:
    def __init__(
        self,
        *,
        evidence: SpeechEvidenceRepository | None = None,
        encryption: SpeechEvidenceEncryptionPort | None = None,
        lineage: MlInternSpeechLineageService | None = None,
        artifacts: SpeechTemporaryArtifactCleanupPort | None = None,
        clock_ms: Callable[[], int] | None = None,
        audit: SemanticMediaAuditPort | None = None,
    ) -> None:
        self._evidence = evidence or get_speech_evidence_repository()
        self._encryption = encryption or get_speech_evidence_encryption_port()
        self._lineage = lineage or get_ml_intern_speech_lineage_service()
        self._artifacts = artifacts or DatabaseOnlySpeechArtifactCleanup()
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._audit = audit

    def run_once(
        self,
        *,
        limit: int = 100,
        now_ms: int | None = None,
        principal: VoicePrincipal | None = None,
    ) -> SpeechEvidenceCleanupSummary:
        if not 1 <= limit <= 1000:
            raise SpeechEvidenceCleanupError("speech_cleanup_limit_invalid")
        now = int(now_ms if now_ms is not None else self._clock_ms())
        expired = self._evidence.list_expired(
            now_ms=now,
            limit=limit,
            tenant_id=principal.tenant_id if principal is not None else None,
            owner_subject=principal.subject if principal is not None else None,
        )
        staged = 0
        skipped = 0
        for record in expired:
            record_principal = VoicePrincipal(record.tenant_id, record.owner_subject)
            try:
                impact = self._lineage.impact(
                    record_principal,
                    root_kind="evidence",
                    root_digest=record.content_digest,
                    revocation_epoch=record.revocation_epoch,
                )
            except Exception:
                skipped += 1
                continue
            active_descendants = [
                node for node in impact.nodes if int(node["depth"]) > 0 and str(node["status"]) == "active"
            ]
            if impact.truncated or active_descendants:
                skipped += 1
                continue
            row = SpeechEvidenceCleanupDB(
                tenant_id=record.tenant_id,
                owner_subject=record.owner_subject,
                evidence_id=record.evidence_id,
                evidence_digest=record.content_digest,
                consent_id=record.consent_id,
                revocation_epoch=record.revocation_epoch,
                impact_decision_digest=impact.impact_digest,
                created_at_ms=now,
                updated_at_ms=now,
            )
            try:
                with Session(engine) as session:
                    session.add(row)
                    audit_event = self._prepare_audit(
                        tenant_id=record.tenant_id,
                        evidence_id=record.evidence_id,
                        evidence_digest=record.content_digest,
                        revocation_epoch=record.revocation_epoch,
                        transition="cleanup_staged",
                        reason_code="speech_evidence_retention_expired",
                        version=record.created_at_ms,
                    )
                    if audit_event is not None:
                        SqlSemanticMediaAuditOutbox.enqueue_in_session(session, audit_event)
                    session.commit()
                staged += 1
            except IntegrityError:
                pass
        completed = 0
        failed = 0
        pending_rows = self._pending(limit=limit, principal=principal)
        for cleanup in pending_rows:
            try:
                if self._process(cleanup.id):
                    completed += 1
            except Exception as exc:
                failed += 1
                self._failure(cleanup.id, str(getattr(exc, "reason_code", "speech_cleanup_step_failed")))
        remaining = len(self._pending(limit=1000, principal=principal))
        return SpeechEvidenceCleanupSummary(staged, completed, remaining, skipped, failed)

    def _process(self, cleanup_id: str) -> bool:
        row = self._get(cleanup_id)
        if row is None or row.state == "completed":
            return False
        now = self._clock_ms()
        if row.state in {"pending", "retry_pending"}:
            audit_event = self._prepare_audit(
                tenant_id=row.tenant_id,
                evidence_id=row.evidence_id,
                evidence_digest=row.evidence_digest,
                revocation_epoch=row.revocation_epoch,
                transition="cleanup_pending",
                reason_code="speech_evidence_retention_claimed",
                version=row.attempt_count + 1,
            )
            claimed = self._evidence.transition(
                tenant_id=row.tenant_id,
                owner_subject=row.owner_subject,
                evidence_id=row.evidence_id,
                expected_states=("quarantined", "rejected", "revoked", "cleanup_pending"),
                target="cleanup_pending",
                now_ms=now,
                audit_event=audit_event,
            )
            if not claimed:
                raise SpeechEvidenceCleanupError("speech_cleanup_active_reference_race")
            self._step(cleanup_id, state="claimed", now_ms=now)
            row = self._get(cleanup_id)
        if row is None:
            raise SpeechEvidenceCleanupError("speech_cleanup_state_missing")
        self._revalidate_impact(row)
        if not row.artifact_cleaned:
            if self._artifacts.cleanup(tenant_id=row.tenant_id, evidence_id=row.evidence_id) is not True:
                raise SpeechEvidenceCleanupError("speech_cleanup_artifact_rejected")
            self._step(cleanup_id, artifact_cleaned=True, state="artifact_cleaned", now_ms=now)
            row = self._get(cleanup_id)
        if row is None:
            raise SpeechEvidenceCleanupError("speech_cleanup_state_missing")
        # Artifact adapters are outside the database transaction and may have
        # raced a Hub publication.  Re-evaluate before destroying the DEK.
        self._revalidate_impact(row)
        if row is not None and not row.key_destroyed:
            record = self._evidence.get(
                tenant_id=row.tenant_id,
                owner_subject=row.owner_subject,
                evidence_id=row.evidence_id,
            )
            if record is not None:
                self._encryption.destroy(record.key_id, tenant_id=row.tenant_id)
            self._step(cleanup_id, key_destroyed=True, state="key_destroyed", now_ms=now)
            row = self._get(cleanup_id)
        if row is None:
            raise SpeechEvidenceCleanupError("speech_cleanup_state_missing")
        # A key-destroy adapter may also have yielded to another Hub writer.
        # The privacy-safe result is to retain ciphertext/tombstone progress
        # and retry, never to claim that active descendants were deleted.
        self._revalidate_impact(row)
        if row is not None and not row.ciphertext_deleted:
            audit_event = self._prepare_audit(
                tenant_id=row.tenant_id,
                evidence_id=row.evidence_id,
                evidence_digest=row.evidence_digest,
                revocation_epoch=row.revocation_epoch,
                transition="deleted",
                reason_code="speech_evidence_retention_expired",
                version=row.attempt_count + 1,
            )
            deleted = self._evidence.delete_ciphertext_and_tombstone(
                tenant_id=row.tenant_id,
                owner_subject=row.owner_subject,
                evidence_id=row.evidence_id,
                evidence_digest=row.evidence_digest,
                consent_id=row.consent_id,
                revocation_epoch=row.revocation_epoch,
                impact_digest=row.impact_decision_digest,
                now_ms=now,
                audit_event=audit_event,
            )
            if not deleted:
                raise SpeechEvidenceCleanupError("speech_cleanup_active_reference_race")
            self._step(
                cleanup_id,
                ciphertext_deleted=True,
                state="completed",
                now_ms=now,
            )
        return True

    def _revalidate_impact(self, row: SpeechEvidenceCleanupDB) -> None:
        """Fence destructive cleanup against newly published descendants.

        The initial decision is intentionally not trusted across artifact/KMS
        calls.  Every destructive boundary gets a fresh transitive view.  A
        content-free digest update is allowed only when the new graph is still
        safe (for example, a previously active descendant was revoked).
        """

        principal = VoicePrincipal(row.tenant_id, row.owner_subject)
        impact = self._lineage.impact(
            principal,
            root_kind="evidence",
            root_digest=row.evidence_digest,
            revocation_epoch=int(row.revocation_epoch),
        )
        active_descendants = any(
            int(node["depth"]) > 0 and str(node["status"]) == "active"
            for node in impact.nodes
        )
        if impact.truncated or active_descendants:
            raise SpeechEvidenceCleanupError("speech_cleanup_active_reference_race")
        if impact.impact_digest != row.impact_decision_digest:
            with Session(engine) as session:
                session.exec(
                    update(SpeechEvidenceCleanupDB)
                    .where(
                        SpeechEvidenceCleanupDB.id == row.id,
                        SpeechEvidenceCleanupDB.state != "completed",
                    )
                    .values(
                        impact_decision_digest=impact.impact_digest,
                        updated_at_ms=self._clock_ms(),
                    )
                )
                session.commit()

    @staticmethod
    def _pending(*, limit: int, principal: VoicePrincipal | None = None) -> list[SpeechEvidenceCleanupDB]:
        with Session(engine) as session:
            statement = select(SpeechEvidenceCleanupDB).where(SpeechEvidenceCleanupDB.state != "completed")
            if principal is not None:
                statement = statement.where(
                    SpeechEvidenceCleanupDB.tenant_id == principal.tenant_id,
                    SpeechEvidenceCleanupDB.owner_subject == principal.subject,
                )
            return list(
                session.exec(statement.order_by(SpeechEvidenceCleanupDB.created_at_ms.asc()).limit(limit)).all()
            )

    @staticmethod
    def _get(cleanup_id: str) -> SpeechEvidenceCleanupDB | None:
        with Session(engine) as session:
            return session.get(SpeechEvidenceCleanupDB, cleanup_id)

    @staticmethod
    def _step(cleanup_id: str, *, state: str, now_ms: int, **values: bool) -> None:
        with Session(engine) as session:
            session.exec(
                update(SpeechEvidenceCleanupDB)
                .where(SpeechEvidenceCleanupDB.id == cleanup_id)
                .values(state=state, updated_at_ms=now_ms, **values)
            )
            session.commit()

    def _failure(self, cleanup_id: str, reason_code: str) -> None:
        with Session(engine) as session:
            session.exec(
                update(SpeechEvidenceCleanupDB)
                .where(SpeechEvidenceCleanupDB.id == cleanup_id)
                .values(
                    state="retry_pending",
                    attempt_count=SpeechEvidenceCleanupDB.attempt_count + 1,
                    last_reason_code=reason_code[:128],
                    updated_at_ms=self._clock_ms(),
                )
            )
            session.commit()

    def _prepare_audit(
        self,
        *,
        tenant_id: str,
        evidence_id: str,
        evidence_digest: str,
        revocation_epoch: int,
        transition: str,
        reason_code: str,
        version: int,
    ):
        if self._audit is None:
            return None
        return self._audit.prepare_transition(
            idempotency_key=f"speech-retention:{evidence_id}:{transition}:{version}",
            tenant_id=tenant_id,
            scope=f"speech-evidence:{evidence_id}",
            event_type="speech_evidence",
            transition=transition,
            reason_code=reason_code,
            epoch=max(1, revocation_epoch + 1),
            contract_ref=evidence_digest,
            job_ref=evidence_id,
        )


__all__ = [
    "DatabaseOnlySpeechArtifactCleanup",
    "SpeechEvidenceCleanupError",
    "SpeechEvidenceCleanupSummary",
    "SpeechEvidenceRetentionCleanupService",
    "SpeechTemporaryArtifactCleanupPort",
]
