"""Persistent CAS repository for speech-evidence consent."""

from __future__ import annotations

import time

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.speech_evidence import SpeechEvidenceConsentDB
from agent.repositories.semantic_media_audit_outbox import SqlSemanticMediaAuditOutbox
from agent.services.semantic_media_audit_service import SemanticMediaAuditEvent
from ananta_contracts.speech_evidence_governance import SpeechEvidenceConsent


class SpeechConsentRepositoryError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class SpeechConsentRepository:
    def get(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        consent_id: str,
    ) -> SpeechEvidenceConsent | None:
        with Session(engine) as session:
            row = session.exec(
                select(SpeechEvidenceConsentDB).where(
                    SpeechEvidenceConsentDB.id == consent_id,
                    SpeechEvidenceConsentDB.tenant_id == tenant_id,
                    SpeechEvidenceConsentDB.owner_subject == owner_subject,
                )
            ).first()
            return _contract(row) if row is not None else None

    def get_for_participant(
        self,
        *,
        tenant_id: str,
        participant_id: str,
        consent_id: str,
    ) -> SpeechEvidenceConsent | None:
        """Read a bilateral consent only for its owner or a bound signer."""

        with Session(engine) as session:
            row = session.exec(
                select(SpeechEvidenceConsentDB).where(
                    SpeechEvidenceConsentDB.id == consent_id,
                    SpeechEvidenceConsentDB.tenant_id == tenant_id,
                )
            ).first()
            if row is None:
                return None
            participants = {str(row.owner_subject)}
            participants.update(str(value) for value in row.required_signers or [])
            return _contract(row) if participant_id in participants else None

    def create(
        self,
        consent: SpeechEvidenceConsent,
        *,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> SpeechEvidenceConsent:
        row = _row(consent)
        try:
            with Session(engine) as session:
                session.add(row)
                if audit_event is not None:
                    SqlSemanticMediaAuditOutbox.enqueue_in_session(session, audit_event)
                session.commit()
        except IntegrityError as exc:
            raise SpeechConsentRepositoryError("speech_consent_id_conflict") from exc
        return consent

    def replace(
        self,
        consent: SpeechEvidenceConsent,
        *,
        expected_version: int,
        expected_revocation_epoch: int,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> SpeechEvidenceConsent:
        values = _values(consent)
        values["updated_at"] = time.time()
        with Session(engine) as session:
            result = session.exec(
                update(SpeechEvidenceConsentDB)
                .where(
                    SpeechEvidenceConsentDB.id == consent.consent_id,
                    SpeechEvidenceConsentDB.tenant_id == consent.tenant_id,
                    SpeechEvidenceConsentDB.owner_subject == consent.owner_subject,
                    SpeechEvidenceConsentDB.consent_version == expected_version,
                    SpeechEvidenceConsentDB.revocation_epoch == expected_revocation_epoch,
                )
                .values(**values)
            )
            if result.rowcount != 1:
                session.rollback()
                raise SpeechConsentRepositoryError("speech_consent_stale_version")
            if audit_event is not None:
                SqlSemanticMediaAuditOutbox.enqueue_in_session(session, audit_event)
            session.commit()
        return consent

    def expire_due(self, *, now_ms: int, limit: int = 100) -> tuple[SpeechEvidenceConsent, ...]:
        if not 1 <= limit <= 1000:
            raise SpeechConsentRepositoryError("speech_consent_limit_invalid")
        with Session(engine) as session:
            rows = session.exec(
                select(SpeechEvidenceConsentDB)
                .where(
                    SpeechEvidenceConsentDB.state == "active",
                    SpeechEvidenceConsentDB.expires_at_ms <= now_ms,
                )
                .order_by(SpeechEvidenceConsentDB.expires_at_ms.asc())
                .limit(limit)
            ).all()
            return tuple(_contract(row) for row in rows)


def _row(consent: SpeechEvidenceConsent) -> SpeechEvidenceConsentDB:
    return SpeechEvidenceConsentDB(id=consent.consent_id, **_values(consent))


def _values(consent: SpeechEvidenceConsent) -> dict[str, object]:
    return {
        "tenant_id": consent.tenant_id,
        "owner_subject": consent.owner_subject,
        "speaker_id": consent.speaker_id,
        "recipient_id": consent.recipient_id,
        "pair_id": consent.pair_id,
        "session_id": consent.session_id,
        "session_epoch": consent.session_epoch,
        "direction": consent.direction,
        "purpose": consent.purpose,
        "scope_digest": consent.scope_digest,
        "consent_digest": consent.consent_digest,
        "scope_payload": consent.scope_mapping(),
        "required_signers": list(consent.required_signers),
        "signature_digests": consent.signatures,
        "state": consent.state,
        "consent_version": consent.consent_version,
        "revocation_epoch": consent.revocation_epoch,
        "issued_at_ms": consent.issued_at_ms,
        "expires_at_ms": consent.expires_at_ms,
    }


def _contract(row: SpeechEvidenceConsentDB) -> SpeechEvidenceConsent:
    return SpeechEvidenceConsent.from_mapping(
        {
            "schema": "ananta.speech-evidence-consent.v1",
            "consent_id": row.id,
            **dict(row.scope_payload or {}),
            "consent_version": int(row.consent_version),
            "revocation_epoch": int(row.revocation_epoch),
            "issued_at_ms": int(row.issued_at_ms),
            "expires_at_ms": int(row.expires_at_ms),
            "state": row.state,
            "required_signers": list(row.required_signers or []),
            "signatures": dict(row.signature_digests or {}),
        }
    )


_repository = SpeechConsentRepository()


def get_speech_consent_repository() -> SpeechConsentRepository:
    return _repository


__all__ = [
    "SpeechConsentRepository",
    "SpeechConsentRepositoryError",
    "get_speech_consent_repository",
]
