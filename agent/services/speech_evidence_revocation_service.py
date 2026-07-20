"""Transitive, idempotent speech evidence revocation owned by the Hub."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.speech_evidence import SpeechCurationTaskDB, SpeechEvidenceRevocationDB
from agent.repositories.semantic_media_audit_outbox import SqlSemanticMediaAuditOutbox
from agent.repositories.speech_evidence import (
    SpeechEvidenceRecord,
    SpeechEvidenceRepository,
    get_speech_evidence_repository,
)
from agent.repositories.speech_evidence_lineage import (
    SpeechEvidenceLineageRepository,
    get_speech_evidence_lineage_repository,
)
from agent.services.ml_intern_speech_lineage_service import (
    MlInternSpeechLineageService,
    get_ml_intern_speech_lineage_service,
)
from agent.services.ml_intern_speech_revocation_service import (
    MlInternSpeechRevocationService,
    SpeechTrainingRevocationOutcome,
)
from agent.services.semantic_media_audit_service import (
    SemanticMediaAuditEvent,
    SemanticMediaAuditPort,
)
from agent.services.speech_evidence_consent_service import (
    SpeechEvidenceConsentService,
    get_speech_evidence_consent_service,
)
from agent.services.speech_evidence_encryption_port import (
    SpeechEvidenceEncryptionPort,
    get_speech_evidence_encryption_port,
)
from agent.services.voice_governance_domain import VoicePrincipal
from ananta_contracts.speech_evidence_governance import SpeechEvidenceGovernanceError


class SpeechEvidenceRevocationError(RuntimeError):
    def __init__(self, reason_code: str, *, status_code: int = 409) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class SpeechEvidenceRevocationResult:
    evidence_digest: str
    consent_id: str
    revocation_epoch: int
    impact_digest: str
    impacted: tuple[dict[str, object], ...]
    fenced_jobs: tuple[str, ...]
    fenced_adapters: tuple[str, ...]
    unresolved: tuple[tuple[str, str], ...]
    key_destroyed: bool
    remote_state: str
    idempotent_replay: bool = False


@dataclass(frozen=True)
class SpeechConsentRevocationCascadeResult:
    consent_id: str
    scanned_count: int
    revoked_count: int
    replayed_count: int
    unresolved_count: int
    truncated: bool
    reason_codes: tuple[str, ...]

    def public_dict(self) -> dict[str, object]:
        return {
            "consent_id": self.consent_id,
            "scanned_count": self.scanned_count,
            "revoked_count": self.revoked_count,
            "replayed_count": self.replayed_count,
            "unresolved_count": self.unresolved_count,
            "truncated": self.truncated,
            "reason_codes": list(self.reason_codes),
        }


class SpeechEvidenceRevocationService:
    def __init__(
        self,
        *,
        evidence: SpeechEvidenceRepository | None = None,
        consent: SpeechEvidenceConsentService | None = None,
        encryption: SpeechEvidenceEncryptionPort | None = None,
        lineage: MlInternSpeechLineageService | None = None,
        lineage_repository: SpeechEvidenceLineageRepository | None = None,
        training: MlInternSpeechRevocationService | None = None,
        audit: SemanticMediaAuditPort | None = None,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self._evidence = evidence or get_speech_evidence_repository()
        self._consent = consent or get_speech_evidence_consent_service()
        self._encryption = encryption or get_speech_evidence_encryption_port()
        self._lineage = lineage or get_ml_intern_speech_lineage_service()
        self._lineage_repository = lineage_repository or get_speech_evidence_lineage_repository()
        self._training = training or MlInternSpeechRevocationService()
        self._audit = audit
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)

    def revoke(
        self,
        principal: VoicePrincipal,
        evidence_id: str,
        *,
        expected_consent_version: int,
        reason_code: str,
        contributor_id: str | None = None,
        authority: str = "hub",
    ) -> SpeechEvidenceRevocationResult:
        if authority != "hub":
            raise SpeechEvidenceRevocationError("speech_revocation_hub_authority_required", status_code=403)
        if not reason_code.startswith("speech_") or len(reason_code) > 128:
            raise SpeechEvidenceRevocationError("speech_revocation_reason_invalid", status_code=422)
        record = self._evidence.get(
            tenant_id=principal.tenant_id, owner_subject=principal.subject, evidence_id=evidence_id
        )
        if record is None:
            raise SpeechEvidenceRevocationError("speech_evidence_not_found", status_code=404)
        replay = self._existing(principal, record.content_digest)
        if replay is not None:
            return self._resume_existing(principal, record, replay)
        current_consent = self._consent.get(principal, record.consent_id)
        if current_consent.state == "active":
            try:
                revoked_consent = self._consent.revoke(
                    principal,
                    record.consent_id,
                    expected_version=expected_consent_version,
                    contributor_id=contributor_id,
                )
            except SpeechEvidenceGovernanceError as exc:
                # A parallel revoker may have won the consent CAS after our
                # read.  Join only the exact monotonic revoked state; every
                # other stale/mismatched claim remains fail-closed.
                concurrent = self._consent.get(principal, record.consent_id)
                if concurrent.state != "revoked" or concurrent.revocation_epoch <= record.revocation_epoch:
                    raise SpeechEvidenceRevocationError(exc.reason_code, status_code=exc.status_code) from exc
                revoked_consent = concurrent
        elif current_consent.state == "revoked" and current_consent.revocation_epoch > record.revocation_epoch:
            # Resume a crash after consent commit but before impact/key cleanup.
            revoked_consent = current_consent
        else:
            raise SpeechEvidenceRevocationError("speech_revocation_consent_state_invalid")
        revocation_audit = self._prepare_audit(
            principal,
            record=record,
            transition="revoked",
            reason_code="speech_evidence_revoked",
            revocation_epoch=revoked_consent.revocation_epoch,
            idempotency_key=(
                f"speech-evidence:revoked:{record.evidence_id}:"
                f"{revoked_consent.revocation_epoch}"
            ),
        )
        with Session(engine) as session:
            session.exec(
                update(SpeechCurationTaskDB)
                .where(
                    SpeechCurationTaskDB.tenant_id == principal.tenant_id,
                    SpeechCurationTaskDB.owner_subject == principal.subject,
                    SpeechCurationTaskDB.consent_id == record.consent_id,
                    SpeechCurationTaskDB.state.in_(["pending_queue", "queued", "running", "publishing"]),
                )
                .values(
                    state="cancelled",
                    fencing_token=SpeechCurationTaskDB.fencing_token + 1,
                    updated_at_ms=self._clock_ms(),
                )
            )
            session.commit()
        try:
            impact = self._lineage.impact(
                principal,
                root_kind="evidence",
                root_digest=record.content_digest,
                revocation_epoch=revoked_consent.revocation_epoch,
            )
        except Exception as exc:
            raise SpeechEvidenceRevocationError(
                str(getattr(exc, "reason_code", "speech_revocation_lineage_unavailable")), status_code=503
            ) from exc
        fences = self._training.fence_impact(principal, impact)
        now = self._clock_ms()
        keys_destroyed = self._encryption.destroy(record.key_id, tenant_id=principal.tenant_id)
        self._evidence.transition(
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            evidence_id=record.evidence_id,
            expected_states=("quarantined", "admitted", "rejected", "accepted"),
            target="revoked",
            now_ms=now,
            audit_event=revocation_audit,
        )
        remote_state = (
            "unresolved"
            if any(str(node["kind"]) in {"export", "receipt"} for node in impact.nodes)
            else "not_requested"
        )
        tombstone = SpeechEvidenceRevocationDB(
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            evidence_digest=record.content_digest,
            consent_id=record.consent_id,
            revocation_epoch=revoked_consent.revocation_epoch,
            reason_code=reason_code,
            impact_digest=impact.impact_digest,
            remote_state=remote_state,
            created_at_ms=now,
            updated_at_ms=now,
        )
        try:
            with Session(engine) as session:
                session.add(tombstone)
                session.commit()
                session.refresh(tombstone)
        except IntegrityError:
            existing = self._existing(principal, record.content_digest)
            if existing is None:
                raise
            tombstone = existing
        current_record = self._evidence.get(
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            evidence_id=record.evidence_id,
        )
        keys_destroyed = keys_destroyed or (
            current_record is not None and current_record.state in {"revoked", "deleted"}
        )
        self._lineage_repository.mark_status(
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            nodes=((str(node["kind"]), str(node["digest"])) for node in impact.nodes),
            status="revoked",
            revocation_epoch=revoked_consent.revocation_epoch,
            now_ms=now,
        )
        return self._result(tombstone, impact.nodes, fences, keys_destroyed, False)

    def revoke_consent(
        self,
        principal: VoicePrincipal,
        consent_id: str,
        *,
        expected_consent_version: int,
        contributor_id: str | None = None,
        limit: int = 1000,
    ) -> SpeechConsentRevocationCascadeResult:
        """Synchronously fence a bounded consent worklist and expose honest remainder."""

        if not 1 <= limit <= 1000:
            raise SpeechEvidenceRevocationError("speech_revocation_consent_limit_invalid", status_code=422)
        records = self._evidence.list_by_consent(
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            consent_id=consent_id,
            limit=limit + 1,
        )
        truncated = len(records) > limit
        revoked = 0
        replayed = 0
        unresolved = 1 if truncated else 0
        reasons: set[str] = {"speech_revocation_consent_worklist_truncated"} if truncated else set()
        for record in records[:limit]:
            try:
                result = self.revoke(
                    principal,
                    record.evidence_id,
                    expected_consent_version=expected_consent_version,
                    reason_code="speech_consent_revoked",
                    contributor_id=contributor_id,
                )
            except SpeechEvidenceRevocationError as exc:
                unresolved += 1
                reasons.add(exc.reason_code)
                continue
            revoked += 1
            replayed += int(result.idempotent_replay)
            if result.unresolved or result.remote_state == "unresolved":
                unresolved += 1
                reasons.add("speech_revocation_downstream_unresolved")
        return SpeechConsentRevocationCascadeResult(
            consent_id=consent_id,
            scanned_count=min(len(records), limit),
            revoked_count=revoked,
            replayed_count=replayed,
            unresolved_count=unresolved,
            truncated=truncated,
            reason_codes=tuple(sorted(reasons)),
        )

    def _resume_existing(
        self,
        principal: VoicePrincipal,
        record: SpeechEvidenceRecord,
        tombstone: SpeechEvidenceRevocationDB,
    ) -> SpeechEvidenceRevocationResult:
        """Idempotently finish every side effect after a tombstone commit."""

        try:
            impact = self._lineage.impact(
                principal,
                root_kind="evidence",
                root_digest=record.content_digest,
                revocation_epoch=int(tombstone.revocation_epoch),
            )
        except Exception as exc:
            raise SpeechEvidenceRevocationError(
                str(getattr(exc, "reason_code", "speech_revocation_lineage_unavailable")),
                status_code=503,
            ) from exc
        fences = self._training.fence_impact(principal, impact)
        now = self._clock_ms()
        destroyed_now = self._encryption.destroy(record.key_id, tenant_id=principal.tenant_id)
        revocation_audit = self._prepare_audit(
            principal,
            record=record,
            transition="revoked",
            reason_code="speech_evidence_revoked",
            revocation_epoch=int(tombstone.revocation_epoch),
            idempotency_key=(
                f"speech-evidence:revoked:{record.evidence_id}:"
                f"{int(tombstone.revocation_epoch)}"
            ),
        )
        self._evidence.transition(
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            evidence_id=record.evidence_id,
            expected_states=("quarantined", "admitted", "rejected", "accepted", "revoked"),
            target="revoked",
            now_ms=now,
            audit_event=revocation_audit,
        )
        self._lineage_repository.mark_status(
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            nodes=((str(node["kind"]), str(node["digest"])) for node in impact.nodes),
            status="revoked",
            revocation_epoch=int(tombstone.revocation_epoch),
            now_ms=now,
        )
        if tombstone.impact_digest != impact.impact_digest:
            with Session(engine) as session:
                session.exec(
                    update(SpeechEvidenceRevocationDB)
                    .where(SpeechEvidenceRevocationDB.id == tombstone.id)
                    .values(impact_digest=impact.impact_digest, updated_at_ms=now)
                )
                session.commit()
            refreshed = self._existing(principal, record.content_digest)
            if refreshed is not None:
                tombstone = refreshed
        return self._result(
            tombstone,
            impact.nodes,
            fences,
            destroyed_now or record.state in {"revoked", "deleted"},
            True,
        )

    def stage_remote_request(
        self,
        principal: VoicePrincipal,
        *,
        evidence_digest: str,
        request_digest: str,
        signature_verified: bool,
    ) -> None:
        if signature_verified is not True or not _digest(request_digest):
            raise SpeechEvidenceRevocationError("speech_revocation_remote_signature_invalid", status_code=403)
        with Session(engine) as session:
            result = session.exec(
                update(SpeechEvidenceRevocationDB)
                .where(
                    SpeechEvidenceRevocationDB.tenant_id == principal.tenant_id,
                    SpeechEvidenceRevocationDB.owner_subject == principal.subject,
                    SpeechEvidenceRevocationDB.evidence_digest == evidence_digest,
                )
                .values(
                    remote_state="requested",
                    remote_request_digest=request_digest,
                    updated_at_ms=self._clock_ms(),
                )
            )
            if result.rowcount != 1:
                session.rollback()
                raise SpeechEvidenceRevocationError("speech_revocation_not_found", status_code=404)
            row = session.exec(
                select(SpeechEvidenceRevocationDB).where(
                    SpeechEvidenceRevocationDB.tenant_id == principal.tenant_id,
                    SpeechEvidenceRevocationDB.owner_subject == principal.subject,
                    SpeechEvidenceRevocationDB.evidence_digest == evidence_digest,
                )
            ).one()
            audit_event = self._prepare_remote_audit(
                principal,
                row=row,
                transition="remote_revocation_requested",
                reason_code="speech_revocation_remote_requested",
                idempotency_key=f"speech-revocation:remote-request:{evidence_digest}:{request_digest}",
            )
            if audit_event is not None:
                SqlSemanticMediaAuditOutbox.enqueue_in_session(session, audit_event)
            session.commit()

    def acknowledge_remote(
        self,
        principal: VoicePrincipal,
        *,
        evidence_digest: str,
        request_digest: str,
        ack_digest: str,
        signature_verified: bool,
    ) -> None:
        if signature_verified is not True or not _digest(ack_digest):
            raise SpeechEvidenceRevocationError("speech_revocation_remote_ack_invalid", status_code=403)
        with Session(engine) as session:
            result = session.exec(
                update(SpeechEvidenceRevocationDB)
                .where(
                    SpeechEvidenceRevocationDB.tenant_id == principal.tenant_id,
                    SpeechEvidenceRevocationDB.owner_subject == principal.subject,
                    SpeechEvidenceRevocationDB.evidence_digest == evidence_digest,
                    SpeechEvidenceRevocationDB.remote_state == "requested",
                    SpeechEvidenceRevocationDB.remote_request_digest == request_digest,
                )
                .values(remote_state="acknowledged", remote_ack_digest=ack_digest, updated_at_ms=self._clock_ms())
            )
            if result.rowcount != 1:
                session.rollback()
                raise SpeechEvidenceRevocationError("speech_revocation_remote_request_mismatch")
            row = session.exec(
                select(SpeechEvidenceRevocationDB).where(
                    SpeechEvidenceRevocationDB.tenant_id == principal.tenant_id,
                    SpeechEvidenceRevocationDB.owner_subject == principal.subject,
                    SpeechEvidenceRevocationDB.evidence_digest == evidence_digest,
                )
            ).one()
            audit_event = self._prepare_remote_audit(
                principal,
                row=row,
                transition="remote_revocation_acknowledged",
                reason_code="speech_revocation_remote_acknowledged",
                idempotency_key=f"speech-revocation:remote-ack:{evidence_digest}:{ack_digest}",
            )
            if audit_event is not None:
                SqlSemanticMediaAuditOutbox.enqueue_in_session(session, audit_event)
            session.commit()

    def _prepare_audit(
        self,
        principal: VoicePrincipal,
        *,
        record: SpeechEvidenceRecord,
        transition: str,
        reason_code: str,
        revocation_epoch: int,
        idempotency_key: str,
    ) -> SemanticMediaAuditEvent | None:
        if self._audit is None:
            return None
        try:
            return self._audit.prepare_transition(
                idempotency_key=idempotency_key,
                tenant_id=principal.tenant_id,
                scope=f"speech-evidence:{record.session_id}",
                event_type="speech_evidence",
                transition=transition,
                reason_code=reason_code,
                epoch=max(1, int(revocation_epoch)),
                job_ref=record.evidence_id,
            )
        except Exception as exc:
            raise SpeechEvidenceRevocationError(
                "speech_revocation_audit_unavailable",
                status_code=503,
            ) from exc

    def _prepare_remote_audit(
        self,
        principal: VoicePrincipal,
        *,
        row: SpeechEvidenceRevocationDB,
        transition: str,
        reason_code: str,
        idempotency_key: str,
    ) -> SemanticMediaAuditEvent | None:
        if self._audit is None:
            return None
        try:
            return self._audit.prepare_transition(
                idempotency_key=idempotency_key,
                tenant_id=principal.tenant_id,
                scope=f"speech-evidence-revocation:{row.evidence_digest}",
                event_type="speech_evidence",
                transition=transition,
                reason_code=reason_code,
                epoch=max(1, int(row.revocation_epoch)),
                job_ref=row.evidence_digest,
            )
        except Exception as exc:
            raise SpeechEvidenceRevocationError(
                "speech_revocation_audit_unavailable",
                status_code=503,
            ) from exc

    @staticmethod
    def _existing(principal: VoicePrincipal, digest: str) -> SpeechEvidenceRevocationDB | None:
        with Session(engine) as session:
            return session.exec(
                select(SpeechEvidenceRevocationDB).where(
                    SpeechEvidenceRevocationDB.tenant_id == principal.tenant_id,
                    SpeechEvidenceRevocationDB.owner_subject == principal.subject,
                    SpeechEvidenceRevocationDB.evidence_digest == digest,
                )
            ).first()

    @staticmethod
    def _result(
        row: SpeechEvidenceRevocationDB,
        impacted,
        fences: SpeechTrainingRevocationOutcome,
        key_destroyed: bool,
        replay: bool,
    ) -> SpeechEvidenceRevocationResult:
        return SpeechEvidenceRevocationResult(
            evidence_digest=row.evidence_digest,
            consent_id=row.consent_id,
            revocation_epoch=int(row.revocation_epoch),
            impact_digest=row.impact_digest,
            impacted=tuple(dict(node) for node in impacted),
            fenced_jobs=fences.fenced_jobs,
            fenced_adapters=fences.fenced_adapters,
            unresolved=fences.unresolved,
            key_destroyed=key_destroyed,
            remote_state=row.remote_state,
            idempotent_replay=replay,
        )


def _digest(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


_service: SpeechEvidenceRevocationService | None = None


def get_speech_evidence_revocation_service() -> SpeechEvidenceRevocationService:
    global _service
    try:
        from flask import current_app, has_app_context

        if has_app_context():
            configured = current_app.extensions.get("speech_evidence_revocation_service")
            if isinstance(configured, SpeechEvidenceRevocationService):
                return configured
            configured = SpeechEvidenceRevocationService(
                audit=current_app.extensions.get("semantic_media_audit_recorder")
            )
            current_app.extensions["speech_evidence_revocation_service"] = configured
            return configured
    except RuntimeError:
        pass
    if _service is None:
        _service = SpeechEvidenceRevocationService()
    return _service


__all__ = [
    "SpeechConsentRevocationCascadeResult",
    "SpeechEvidenceRevocationError",
    "SpeechEvidenceRevocationResult",
    "SpeechEvidenceRevocationService",
    "get_speech_evidence_revocation_service",
]
