"""Transactional encrypted-quarantine repository for speech evidence."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.speech_evidence import (
    SpeechEvidenceConsentDB,
    SpeechEvidenceDB,
    SpeechEvidenceRevocationDB,
)
from agent.repositories.semantic_media_audit_outbox import SqlSemanticMediaAuditOutbox
from agent.repositories.speech_evidence_lineage import (
    SpeechEvidenceLineageRepository,
    SpeechLineageNode,
    get_speech_evidence_lineage_repository,
)
from agent.services.semantic_media_audit_service import SemanticMediaAuditEvent
from ananta_contracts.speech_evidence_crypto import SpeechEvidenceCiphertext
from ananta_contracts.speech_evidence_governance import SpeechEvidenceConsent


class SpeechEvidenceRepositoryError(RuntimeError):
    def __init__(self, reason_code: str, *, status_code: int = 409) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class SpeechEvidenceQuotas:
    max_payload_bytes: int = 16 * 1024 * 1024
    max_tenant_bytes: int = 2 * 1024 * 1024 * 1024
    max_pair_bytes: int = 512 * 1024 * 1024
    max_tenant_records: int = 100_000
    max_pair_records: int = 20_000
    max_ttl_seconds: int = 7 * 86_400


@dataclass(frozen=True)
class SpeechEvidenceRecord:
    evidence_id: str
    tenant_id: str
    owner_subject: str
    pair_id: str
    session_id: str
    session_epoch: int
    speaker_scope_digest: str
    utterance_family_id: str
    evidence_class: str
    purpose: str
    consent_id: str
    consent_version: int
    revocation_epoch: int
    content_digest: str
    source_digest: str
    provenance_digest: str
    key_id: str
    byte_count: int
    retention_seconds: int
    state: str
    admission_digest: str | None
    expires_at_ms: int
    created_at_ms: int

    def public_dict(self) -> dict[str, object]:
        return {
            "evidence_id": self.evidence_id,
            "pair_id": self.pair_id,
            "session_id": self.session_id,
            "session_epoch": self.session_epoch,
            "speaker_scope_digest": self.speaker_scope_digest,
            "utterance_family_id": self.utterance_family_id,
            "evidence_class": self.evidence_class,
            "purpose": self.purpose,
            "consent_id": self.consent_id,
            "consent_version": self.consent_version,
            "revocation_epoch": self.revocation_epoch,
            "content_digest": self.content_digest,
            "source_digest": self.source_digest,
            "provenance_digest": self.provenance_digest,
            "byte_count": self.byte_count,
            "retention_seconds": self.retention_seconds,
            "state": self.state,
            "admission_digest": self.admission_digest,
            "expires_at_ms": self.expires_at_ms,
            "created_at_ms": self.created_at_ms,
        }


class SpeechEvidenceRepository:
    def __init__(
        self,
        quotas: SpeechEvidenceQuotas | None = None,
        *,
        lineage: SpeechEvidenceLineageRepository | None = None,
    ) -> None:
        self.quotas = quotas or SpeechEvidenceQuotas()
        self._lineage = lineage or get_speech_evidence_lineage_repository()

    def find_digest(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        pair_id: str,
        session_id: str,
        evidence_class: str,
        content_digest: str,
    ) -> SpeechEvidenceRecord | None:
        with Session(engine) as session:
            row = session.exec(
                select(SpeechEvidenceDB).where(
                    SpeechEvidenceDB.tenant_id == tenant_id,
                    SpeechEvidenceDB.owner_subject == owner_subject,
                    SpeechEvidenceDB.pair_id == pair_id,
                    SpeechEvidenceDB.session_id == session_id,
                    SpeechEvidenceDB.evidence_class == evidence_class,
                    SpeechEvidenceDB.content_digest == content_digest,
                )
            ).first()
            return _record(row) if row is not None else None

    def create(
        self,
        *,
        evidence_id: str,
        consent: SpeechEvidenceConsent,
        envelope: SpeechEvidenceCiphertext,
        scoped_content_digest: str,
        source_digest: str,
        provenance_digest: str,
        speaker_scope_digest: str,
        utterance_family_id: str,
        evidence_class: str,
        retention_seconds: int,
        expires_at_ms: int,
        created_at_ms: int,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> tuple[SpeechEvidenceRecord, bool]:
        payload_bytes = max(0, len(envelope.ciphertext) - 16)
        if payload_bytes < 1 or payload_bytes > self.quotas.max_payload_bytes:
            raise SpeechEvidenceRepositoryError("speech_evidence_payload_quota_exceeded", status_code=413)
        if retention_seconds > self.quotas.max_ttl_seconds:
            raise SpeechEvidenceRepositoryError("speech_evidence_ttl_quota_exceeded", status_code=422)
        with Session(engine) as session:
            # Consent, revocation tombstone and quota checks share the write
            # transaction.  Locking the first consent row gives every writer
            # for one tenant the same serialization point, including writers
            # using different pair/consent rows, so aggregate quotas cannot be
            # oversubscribed by concurrent inserts.
            tenant_guard = session.exec(
                select(SpeechEvidenceConsentDB.id)
                .where(SpeechEvidenceConsentDB.tenant_id == consent.tenant_id)
                .order_by(SpeechEvidenceConsentDB.id)
                .limit(1)
                .with_for_update()
            ).first()
            if tenant_guard is None:
                raise SpeechEvidenceRepositoryError("speech_evidence_consent_stale", status_code=409)
            # The current consent row is the revoke-vs-store fence.
            current = session.exec(
                select(SpeechEvidenceConsentDB)
                .where(
                    SpeechEvidenceConsentDB.id == consent.consent_id,
                    SpeechEvidenceConsentDB.tenant_id == consent.tenant_id,
                    SpeechEvidenceConsentDB.owner_subject == consent.owner_subject,
                )
                .with_for_update()
            ).first()
            if current is None or (
                current.state != "active"
                or current.consent_version != consent.consent_version
                or current.revocation_epoch != consent.revocation_epoch
                or current.consent_digest != consent.consent_digest
                or current.expires_at_ms <= created_at_ms
            ):
                raise SpeechEvidenceRepositoryError("speech_evidence_consent_stale", status_code=409)
            tombstone = session.exec(
                select(SpeechEvidenceRevocationDB.id).where(
                    SpeechEvidenceRevocationDB.tenant_id == consent.tenant_id,
                    SpeechEvidenceRevocationDB.evidence_digest == scoped_content_digest,
                )
            ).first()
            if tombstone is not None:
                raise SpeechEvidenceRepositoryError("speech_evidence_revoked_tombstone", status_code=410)
            existing = session.exec(
                select(SpeechEvidenceDB).where(
                    SpeechEvidenceDB.tenant_id == consent.tenant_id,
                    SpeechEvidenceDB.owner_subject == consent.owner_subject,
                    SpeechEvidenceDB.pair_id == consent.pair_id,
                    SpeechEvidenceDB.session_id == consent.session_id,
                    SpeechEvidenceDB.evidence_class == evidence_class,
                    SpeechEvidenceDB.content_digest == scoped_content_digest,
                )
            ).first()
            if existing is not None:
                if (
                    existing.provenance_digest != provenance_digest
                    or existing.utterance_family_id != utterance_family_id
                ):
                    raise SpeechEvidenceRepositoryError("speech_evidence_digest_binding_conflict")
                return _record(existing), False
            tenant_count, tenant_bytes = session.exec(
                select(func.count(SpeechEvidenceDB.id), func.coalesce(func.sum(SpeechEvidenceDB.byte_count), 0)).where(
                    SpeechEvidenceDB.tenant_id == consent.tenant_id,
                    SpeechEvidenceDB.state.notin_(["deleted", "rejected"]),
                )
            ).one()
            pair_count, pair_bytes = session.exec(
                select(func.count(SpeechEvidenceDB.id), func.coalesce(func.sum(SpeechEvidenceDB.byte_count), 0)).where(
                    SpeechEvidenceDB.tenant_id == consent.tenant_id,
                    SpeechEvidenceDB.pair_id == consent.pair_id,
                    SpeechEvidenceDB.state.notin_(["deleted", "rejected"]),
                )
            ).one()
            if int(tenant_count) + 1 > self.quotas.max_tenant_records:
                raise SpeechEvidenceRepositoryError("speech_evidence_tenant_record_quota_exceeded", status_code=413)
            if int(pair_count) + 1 > self.quotas.max_pair_records:
                raise SpeechEvidenceRepositoryError("speech_evidence_pair_record_quota_exceeded", status_code=413)
            if int(tenant_bytes) + payload_bytes > self.quotas.max_tenant_bytes:
                raise SpeechEvidenceRepositoryError("speech_evidence_tenant_byte_quota_exceeded", status_code=413)
            if int(pair_bytes) + payload_bytes > self.quotas.max_pair_bytes:
                raise SpeechEvidenceRepositoryError("speech_evidence_pair_byte_quota_exceeded", status_code=413)
            row = SpeechEvidenceDB(
                id=evidence_id,
                tenant_id=consent.tenant_id,
                owner_subject=consent.owner_subject,
                pair_id=consent.pair_id,
                session_id=consent.session_id,
                session_epoch=consent.session_epoch,
                speaker_scope_digest=speaker_scope_digest,
                utterance_family_id=utterance_family_id,
                evidence_class=evidence_class,
                purpose=consent.purpose,
                consent_id=consent.consent_id,
                consent_version=consent.consent_version,
                revocation_epoch=consent.revocation_epoch,
                content_digest=scoped_content_digest,
                cipher_content_digest=envelope.content_digest,
                source_digest=source_digest,
                provenance_digest=provenance_digest,
                key_id=envelope.key_id,
                nonce=envelope.nonce,
                ciphertext=envelope.ciphertext,
                byte_count=payload_bytes,
                retention_seconds=retention_seconds,
                state="quarantined",
                expires_at_ms=expires_at_ms,
                created_at_ms=created_at_ms,
                updated_at_ms=created_at_ms,
            )
            session.add(row)
            try:
                # Lineage and outbox perform reads that may trigger an
                # autoflush. Keep them inside the uniqueness-race handler so
                # a concurrent Hub winner resolves to the idempotent record.
                event_digest = self._lineage.stage(
                    session,
                    tenant_id=consent.tenant_id,
                    owner_subject=consent.owner_subject,
                    nodes=(
                        SpeechLineageNode(
                            "evidence",
                            scoped_content_digest,
                            consent_id=consent.consent_id,
                            revocation_epoch=consent.revocation_epoch,
                        ),
                    ),
                    edges=(),
                    now_ms=created_at_ms,
                )
                if audit_event is not None:
                    SqlSemanticMediaAuditOutbox.enqueue_in_session(session, audit_event)
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                # A concurrent idempotent writer may have won the scoped
                # digest uniqueness race after our preflight read.  Resolve
                # that race to the same record; never turn a different
                # provenance/family binding into an idempotent success.
                winner = session.exec(
                    select(SpeechEvidenceDB).where(
                        SpeechEvidenceDB.tenant_id == consent.tenant_id,
                        SpeechEvidenceDB.owner_subject == consent.owner_subject,
                        SpeechEvidenceDB.pair_id == consent.pair_id,
                        SpeechEvidenceDB.session_id == consent.session_id,
                        SpeechEvidenceDB.evidence_class == evidence_class,
                        SpeechEvidenceDB.content_digest == scoped_content_digest,
                    )
                ).first()
                if winner is not None:
                    if (
                        winner.provenance_digest != provenance_digest
                        or winner.utterance_family_id != utterance_family_id
                    ):
                        raise SpeechEvidenceRepositoryError("speech_evidence_digest_binding_conflict") from exc
                    return _record(winner), False
                raise SpeechEvidenceRepositoryError("speech_evidence_write_conflict") from exc
            session.refresh(row)
            record = _record(row)
        self._lineage.process_outbox(
            event_digest=event_digest,
            tenant_id=consent.tenant_id,
            owner_subject=consent.owner_subject,
        )
        return record, True

    def get(self, *, tenant_id: str, owner_subject: str, evidence_id: str) -> SpeechEvidenceRecord | None:
        with Session(engine) as session:
            row = session.exec(
                select(SpeechEvidenceDB).where(
                    SpeechEvidenceDB.id == evidence_id,
                    SpeechEvidenceDB.tenant_id == tenant_id,
                    SpeechEvidenceDB.owner_subject == owner_subject,
                )
            ).first()
            return _record(row) if row is not None else None

    def list_by_consent(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        consent_id: str,
        limit: int = 1000,
    ) -> tuple[SpeechEvidenceRecord, ...]:
        """Return a bounded, deterministic revocation worklist for one consent."""

        if not 1 <= limit <= 1001:
            raise SpeechEvidenceRepositoryError("speech_evidence_consent_list_limit_invalid", status_code=422)
        with Session(engine) as session:
            rows = session.exec(
                select(SpeechEvidenceDB)
                .where(
                    SpeechEvidenceDB.tenant_id == tenant_id,
                    SpeechEvidenceDB.owner_subject == owner_subject,
                    SpeechEvidenceDB.consent_id == consent_id,
                    SpeechEvidenceDB.state.notin_(["deleted", "revoked"]),
                )
                .order_by(SpeechEvidenceDB.created_at_ms.asc(), SpeechEvidenceDB.id.asc())
                .limit(limit)
            ).all()
            return tuple(_record(row) for row in rows)

    def encrypted(self, *, tenant_id: str, owner_subject: str, evidence_id: str) -> SpeechEvidenceCiphertext:
        with Session(engine) as session:
            row = session.exec(
                select(SpeechEvidenceDB).where(
                    SpeechEvidenceDB.id == evidence_id,
                    SpeechEvidenceDB.tenant_id == tenant_id,
                    SpeechEvidenceDB.owner_subject == owner_subject,
                )
            ).first()
            if row is None:
                raise SpeechEvidenceRepositoryError("speech_evidence_not_found", status_code=404)
            if row.state in {"deleted", "revoked"} or not row.ciphertext or not row.nonce:
                raise SpeechEvidenceRepositoryError("speech_evidence_unreadable", status_code=410)
            return SpeechEvidenceCiphertext(
                artifact_ref=row.id,
                artifact_class="evidence",
                tenant_id=row.tenant_id,
                pair_id=row.pair_id,
                purpose=row.purpose,
                session_epoch=row.session_epoch,
                key_epoch=max(1, row.revocation_epoch + 1),
                key_id=row.key_id,
                content_digest=row.cipher_content_digest,
                nonce=bytes(row.nonce),
                ciphertext=bytes(row.ciphertext),
            )

    def transition(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        evidence_id: str,
        expected_states: tuple[str, ...],
        target: str,
        now_ms: int,
        admission_digest: str | None = None,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> bool:
        with Session(engine) as session:
            result = session.exec(
                update(SpeechEvidenceDB)
                .where(
                    SpeechEvidenceDB.id == evidence_id,
                    SpeechEvidenceDB.tenant_id == tenant_id,
                    SpeechEvidenceDB.owner_subject == owner_subject,
                    SpeechEvidenceDB.state.in_(expected_states),
                )
                .values(state=target, admission_digest=admission_digest, updated_at_ms=now_ms)
            )
            if result.rowcount == 1 and audit_event is not None:
                SqlSemanticMediaAuditOutbox.enqueue_in_session(session, audit_event)
            session.commit()
            return result.rowcount == 1

    def list_expired(
        self,
        *,
        now_ms: int,
        limit: int,
        tenant_id: str | None = None,
        owner_subject: str | None = None,
    ) -> tuple[SpeechEvidenceRecord, ...]:
        if not 1 <= limit <= 1000:
            raise SpeechEvidenceRepositoryError("speech_evidence_cleanup_limit_invalid", status_code=422)
        with Session(engine) as session:
            statement = select(SpeechEvidenceDB).where(
                SpeechEvidenceDB.expires_at_ms <= now_ms,
                SpeechEvidenceDB.state.in_(["quarantined", "rejected", "revoked"]),
            )
            if tenant_id is not None:
                statement = statement.where(SpeechEvidenceDB.tenant_id == tenant_id)
            if owner_subject is not None:
                statement = statement.where(SpeechEvidenceDB.owner_subject == owner_subject)
            rows = session.exec(
                statement.order_by(SpeechEvidenceDB.expires_at_ms.asc(), SpeechEvidenceDB.id.asc()).limit(limit)
            ).all()
            return tuple(_record(row) for row in rows)

    def delete_ciphertext(
        self,
        *,
        tenant_id: str,
        evidence_id: str,
        now_ms: int,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> bool:
        with Session(engine) as session:
            result = session.exec(
                update(SpeechEvidenceDB)
                .where(
                    SpeechEvidenceDB.id == evidence_id,
                    SpeechEvidenceDB.tenant_id == tenant_id,
                    SpeechEvidenceDB.state != "accepted",
                )
                .values(ciphertext=b"", nonce=b"", byte_count=0, state="deleted", updated_at_ms=now_ms)
            )
            if result.rowcount == 1 and audit_event is not None:
                SqlSemanticMediaAuditOutbox.enqueue_in_session(session, audit_event)
            session.commit()
            return result.rowcount == 1

    def delete_ciphertext_and_tombstone(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        evidence_id: str,
        evidence_digest: str,
        consent_id: str,
        revocation_epoch: int,
        impact_digest: str,
        now_ms: int,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> bool:
        """Atomically erase encrypted bytes, persist the deny tombstone and audit.

        Physical artifact/KMS adapters run before this closed SQL boundary.
        Once this method commits, no Hub replica can re-admit the scoped digest
        without observing the same tombstone and audit command.
        """

        with Session(engine) as session:
            row = session.exec(
                select(SpeechEvidenceDB)
                .where(
                    SpeechEvidenceDB.id == evidence_id,
                    SpeechEvidenceDB.tenant_id == tenant_id,
                    SpeechEvidenceDB.owner_subject == owner_subject,
                    SpeechEvidenceDB.content_digest == evidence_digest,
                )
                .with_for_update()
            ).first()
            if row is None or row.state == "accepted":
                return False
            row.ciphertext = b""
            row.nonce = b""
            row.byte_count = 0
            row.state = "deleted"
            row.updated_at_ms = now_ms
            session.add(row)
            tombstone = session.exec(
                select(SpeechEvidenceRevocationDB).where(
                    SpeechEvidenceRevocationDB.tenant_id == tenant_id,
                    SpeechEvidenceRevocationDB.evidence_digest == evidence_digest,
                )
            ).first()
            if tombstone is None:
                session.add(
                    SpeechEvidenceRevocationDB(
                        tenant_id=tenant_id,
                        owner_subject=owner_subject,
                        evidence_digest=evidence_digest,
                        consent_id=consent_id,
                        revocation_epoch=revocation_epoch,
                        reason_code="speech_evidence_retention_expired",
                        impact_digest=impact_digest,
                        remote_state="not_requested",
                        created_at_ms=now_ms,
                        updated_at_ms=now_ms,
                    )
                )
            if audit_event is not None:
                SqlSemanticMediaAuditOutbox.enqueue_in_session(session, audit_event)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                winner = session.exec(
                    select(SpeechEvidenceRevocationDB.id).where(
                        SpeechEvidenceRevocationDB.tenant_id == tenant_id,
                        SpeechEvidenceRevocationDB.evidence_digest == evidence_digest,
                    )
                ).first()
                if winner is None:
                    raise
            return True


def _record(row: SpeechEvidenceDB) -> SpeechEvidenceRecord:
    return SpeechEvidenceRecord(
        evidence_id=row.id,
        tenant_id=row.tenant_id,
        owner_subject=row.owner_subject,
        pair_id=row.pair_id,
        session_id=row.session_id,
        session_epoch=int(row.session_epoch),
        speaker_scope_digest=row.speaker_scope_digest,
        utterance_family_id=row.utterance_family_id,
        evidence_class=row.evidence_class,
        purpose=row.purpose,
        consent_id=row.consent_id,
        consent_version=int(row.consent_version),
        revocation_epoch=int(row.revocation_epoch),
        content_digest=row.content_digest,
        source_digest=row.source_digest,
        provenance_digest=row.provenance_digest,
        key_id=row.key_id,
        byte_count=int(row.byte_count),
        retention_seconds=int(row.retention_seconds),
        state=row.state,
        admission_digest=row.admission_digest,
        expires_at_ms=int(row.expires_at_ms),
        created_at_ms=int(row.created_at_ms),
    )


_repository = SpeechEvidenceRepository()


def get_speech_evidence_repository() -> SpeechEvidenceRepository:
    return _repository


__all__ = [
    "SpeechEvidenceQuotas",
    "SpeechEvidenceRecord",
    "SpeechEvidenceRepository",
    "SpeechEvidenceRepositoryError",
    "get_speech_evidence_repository",
]
