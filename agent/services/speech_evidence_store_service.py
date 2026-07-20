"""Application service for the encrypted, short-lived evidence quarantine."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Callable

from agent.repositories.speech_evidence import (
    SpeechEvidenceRecord,
    SpeechEvidenceRepository,
    SpeechEvidenceRepositoryError,
    get_speech_evidence_repository,
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
from voice_runtime.evidence_identity import SpeechEvidenceIdentity

_EVIDENCE_CLASSES = frozenset(
    {"audio", "transcript", "acoustic_features", "speaker_embedding", "correction", "quality_metrics"}
)
_QUARANTINE_GRANTS = frozenset({"capture", "transcript_share", "feature_share", "raw_audio_share"})


class SpeechEvidenceStoreError(RuntimeError):
    def __init__(self, reason_code: str, message: str, *, status_code: int = 422) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(message)


class SpeechEvidenceStoreService:
    def __init__(
        self,
        repository: SpeechEvidenceRepository | None = None,
        consent: SpeechEvidenceConsentService | None = None,
        encryption: SpeechEvidenceEncryptionPort | None = None,
        *,
        digest_key: bytes | None = None,
        clock_ms: Callable[[], int] | None = None,
        audit: SemanticMediaAuditPort | None = None,
    ) -> None:
        self._repository = repository or get_speech_evidence_repository()
        self._consent = consent or get_speech_evidence_consent_service()
        self._encryption = encryption or get_speech_evidence_encryption_port()
        self._digest_key = bytes(digest_key) if digest_key is not None else _configured_digest_key()
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._audit = audit

    def store(
        self,
        principal: VoicePrincipal,
        payload: bytes,
        *,
        claimed_content_digest: str,
        provenance_digest: str,
        identity: SpeechEvidenceIdentity,
        evidence_class: str,
        data_class: str,
        grant: str,
        consent_id: str,
        consent_version: int,
        revocation_epoch: int,
        consent_digest: str,
        speaker_id: str,
        recipient_id: str,
        direction: str,
        pair_id: str,
        session_id: str,
        session_epoch: int,
        purpose: str,
        retention_seconds: int,
        security_mode: str = "trusted_compute",
    ) -> tuple[SpeechEvidenceRecord, bool]:
        body = bytes(payload)
        if evidence_class not in _EVIDENCE_CLASSES or data_class not in _EVIDENCE_CLASSES:
            raise SpeechEvidenceStoreError("speech_evidence_class_invalid", "evidence class is unsupported")
        if (
            identity.algorithm_version != "speech-evidence-commitment-hmac-sha256-v1"
            or identity.pair_id != pair_id
            or identity.session_id != session_id
            or identity.session_epoch != session_epoch
            or identity.speaker_scope != speaker_id
        ):
            raise SpeechEvidenceStoreError(
                "speech_evidence_identity_scope_mismatch",
                "evidence identity does not match pair, session, epoch or speaker",
                status_code=403,
            )
        if grant not in _QUARANTINE_GRANTS:
            raise SpeechEvidenceStoreError(
                "speech_evidence_implicit_curation_forbidden",
                "quarantine cannot create dataset or training authority",
                status_code=403,
            )
        actual_digest = hashlib.sha256(body).hexdigest()
        if not hmac.compare_digest(actual_digest, str(claimed_content_digest or "")):
            raise SpeechEvidenceStoreError(
                "speech_evidence_digest_mismatch", "claimed content digest does not match", status_code=409
            )
        if len(provenance_digest) != 64 or any(ch not in "0123456789abcdef" for ch in provenance_digest):
            raise SpeechEvidenceStoreError("speech_evidence_provenance_invalid", "provenance digest is invalid")
        try:
            current = self._consent.authorize_claim(
                principal,
                consent_id,
                expected_consent_version=consent_version,
                expected_revocation_epoch=revocation_epoch,
                expected_consent_digest=consent_digest,
                grant=grant,
                speaker_id=speaker_id,
                recipient_id=recipient_id,
                direction=direction,
                pair_id=pair_id,
                session_id=session_id,
                session_epoch=session_epoch,
                purpose=purpose,
                data_class=data_class,
            )
        except SpeechEvidenceGovernanceError as exc:
            raise SpeechEvidenceStoreError(exc.reason_code, str(exc), status_code=exc.status_code) from exc
        if (
            isinstance(retention_seconds, bool)
            or retention_seconds < 60
            or retention_seconds > current.retention_seconds
            or retention_seconds > self._repository.quotas.max_ttl_seconds
        ):
            raise SpeechEvidenceStoreError(
                "speech_evidence_retention_invalid", "quarantine retention exceeds consent or policy"
            )
        scoped_digest = hmac.new(self._scope_digest_key(current.scope_digest), body, hashlib.sha256).hexdigest()
        existing = self._repository.find_digest(
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            pair_id=pair_id,
            session_id=session_id,
            evidence_class=evidence_class,
            content_digest=scoped_digest,
        )
        if existing is not None:
            if (
                existing.provenance_digest != provenance_digest
                or existing.utterance_family_id != identity.utterance_family_id
            ):
                raise SpeechEvidenceStoreError(
                    "speech_evidence_digest_binding_conflict", "duplicate content has conflicting provenance"
                )
            try:
                envelope = self._repository.encrypted(
                    tenant_id=principal.tenant_id,
                    owner_subject=principal.subject,
                    evidence_id=existing.evidence_id,
                )
                recovered = self._encryption.decrypt(envelope, security_mode=security_mode)
            except Exception as exc:
                raise SpeechEvidenceStoreError(
                    str(getattr(exc, "reason_code", "speech_evidence_key_unavailable")),
                    "duplicate speech evidence is no longer readable",
                    status_code=int(getattr(exc, "status_code", 410)),
                ) from exc
            if not hmac.compare_digest(recovered, body):
                raise SpeechEvidenceStoreError(
                    "speech_evidence_duplicate_content_mismatch",
                    "duplicate speech evidence failed authenticated equality",
                    status_code=409,
                )
            return existing, False
        evidence_id = f"speech-evidence-{os.urandom(16).hex()}"
        audit_event = self._prepare_audit(
            principal,
            evidence_id=evidence_id,
            session_id=session_id,
            session_epoch=session_epoch,
        )
        key_epoch = current.revocation_epoch + 1
        try:
            envelope = self._encryption.encrypt(
                body,
                artifact_ref=evidence_id,
                artifact_class="evidence",
                tenant_id=principal.tenant_id,
                pair_id=pair_id,
                purpose=purpose,
                session_epoch=session_epoch,
                key_epoch=key_epoch,
                security_mode=security_mode,
            )
        except Exception as exc:
            raise SpeechEvidenceStoreError(
                str(getattr(exc, "reason_code", "speech_evidence_encryption_failed")),
                "speech evidence encryption failed",
                status_code=int(getattr(exc, "status_code", 503)),
            ) from exc
        now = self._clock_ms()
        expires_at = min(now + retention_seconds * 1000, current.expires_at_ms)
        try:
            record, created = self._repository.create(
                evidence_id=evidence_id,
                consent=current,
                envelope=envelope,
                scoped_content_digest=scoped_digest,
                source_digest=identity.source_scope_digest,
                provenance_digest=provenance_digest,
                speaker_scope_digest=identity.speaker_scope_digest,
                utterance_family_id=identity.utterance_family_id,
                evidence_class=evidence_class,
                retention_seconds=retention_seconds,
                expires_at_ms=expires_at,
                created_at_ms=now,
                audit_event=audit_event,
            )
        except Exception as exc:
            # Encryption happens before the repository transaction, therefore
            # every pre-commit failure must destroy the freshly minted DEK.
            # A lineage-outbox delivery failure is different: the evidence
            # row and its outbox entry may already be committed.  Destroying
            # that DEK would turn a recoverable delivery failure into data
            # loss, so distinguish the two states by the opaque evidence ID.
            try:
                persisted = self._repository.get(
                    tenant_id=principal.tenant_id,
                    owner_subject=principal.subject,
                    evidence_id=evidence_id,
                )
            except Exception:
                persisted = None
            if persisted is None:
                self._encryption.destroy(envelope.key_id, tenant_id=principal.tenant_id)
            if isinstance(exc, SpeechEvidenceRepositoryError):
                raise SpeechEvidenceStoreError(exc.reason_code, str(exc), status_code=exc.status_code) from exc
            reason_code = str(getattr(exc, "reason_code", "speech_evidence_write_failed"))
            status_code = int(getattr(exc, "status_code", 503))
            if persisted is not None:
                reason_code = "speech_evidence_lineage_delivery_pending"
                status_code = 503
            raise SpeechEvidenceStoreError(
                reason_code,
                "speech evidence persistence did not complete cleanly",
                status_code=status_code,
            ) from exc
        if not created:
            self._encryption.destroy(envelope.key_id, tenant_id=principal.tenant_id)
        return record, created

    def _prepare_audit(
        self,
        principal: VoicePrincipal,
        *,
        evidence_id: str,
        session_id: str,
        session_epoch: int,
    ) -> SemanticMediaAuditEvent | None:
        if self._audit is None:
            return None
        try:
            return self._audit.prepare_transition(
                idempotency_key=f"speech-evidence:quarantined:{evidence_id}",
                tenant_id=principal.tenant_id,
                scope=f"semantic-media-session:{session_id}",
                event_type="speech_evidence",
                transition="quarantined",
                reason_code="speech_evidence_quarantined",
                epoch=session_epoch,
                job_ref=evidence_id,
            )
        except Exception as exc:
            raise SpeechEvidenceStoreError(
                "semantic_audit_unavailable",
                "speech evidence audit is unavailable",
                status_code=503,
            ) from exc

    def _scope_digest_key(self, scope_digest: str) -> bytes:
        return hmac.new(
            self._digest_key,
            f"speech-evidence-content-v1\0{scope_digest}".encode(),
            hashlib.sha256,
        ).digest()


def _configured_digest_key() -> bytes:
    from agent.config import settings

    secret = str(settings.secret_key or "")
    if not secret:
        raise SpeechEvidenceStoreError(
            "speech_evidence_digest_key_missing", "speech evidence digest key is not configured", status_code=500
        )
    return hashlib.sha256(f"ananta-speech-evidence-digest-v1:{secret}".encode()).digest()


_service: SpeechEvidenceStoreService | None = None
_audit: SemanticMediaAuditPort | None = None


def get_speech_evidence_store_service() -> SpeechEvidenceStoreService:
    global _service
    if _service is None:
        _service = SpeechEvidenceStoreService(audit=_audit)
    return _service


def configure_speech_evidence_store_audit(audit: SemanticMediaAuditPort) -> None:
    global _audit, _service
    _audit = audit
    _service = None


__all__ = [
    "SpeechEvidenceStoreError",
    "SpeechEvidenceStoreService",
    "configure_speech_evidence_store_audit",
    "get_speech_evidence_store_service",
]
