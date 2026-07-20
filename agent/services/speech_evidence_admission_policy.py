"""Quality, provenance and privacy gate before Hub speech curation."""

from __future__ import annotations

import hashlib
import hmac
import re
import time
from dataclasses import dataclass
from typing import Callable, Mapping, Protocol

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.speech_evidence import SpeechEvidenceAdmissionDB, SpeechEvidenceDB
from agent.repositories.semantic_media_audit_outbox import SqlSemanticMediaAuditOutbox
from agent.repositories.speech_evidence import (
    SpeechEvidenceRecord,
    SpeechEvidenceRepository,
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
from ananta_contracts.speech_evidence_governance import canonical_json
from voice_runtime.evidence_quality import SpeechEvidenceQualityError, SpeechEvidenceQualityPolicy

_EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\s().-]?){8,16}(?!\w)")
_SECRET = re.compile(r"(?i)(?:api[_-]?key|secret|password|token)\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{8,}")
_PROMPT_INJECTION = re.compile(
    r"(?i)(?:ignore\s+(?:all\s+)?previous|system\s+prompt|developer\s+message|bypass\s+(?:the\s+)?policy)"
)


class SpeechEvidenceAuthorityPort(Protocol):
    """M1-backed signature, permission and epoch authority."""

    def authorize(
        self,
        *,
        tenant_id: str,
        peer_id: str,
        pair_id: str,
        session_id: str,
        session_epoch: int,
        direction: str,
        data_class: str,
        purpose: str,
        evidence_digest: str,
        signature: str,
    ) -> tuple[bool, str]: ...


class UnavailableSpeechEvidenceAuthority:
    def authorize(self, **_kwargs: object) -> tuple[bool, str]:
        return False, "speech_evidence_authority_unavailable"


class M1SpeechEvidenceAuthority:
    """Adapter over the M1 capability and authoritative epoch services.

    ``signature`` uses ``<grant-id>:<hex-hmac>``.  The HMAC binds the
    capability's Hub signature to the scoped evidence digest, so replay under
    another capability or payload fails without introducing peer credentials
    into Hub tasks.
    """

    def __init__(self, *, permission_service, grant_lookup: Callable[[str], object], epoch_service=None) -> None:
        from agent.services.webrtc_epoch_service import get_webrtc_epoch_service

        self._permissions = permission_service
        self._lookup = grant_lookup
        self._epochs = epoch_service or get_webrtc_epoch_service()

    def authorize(self, **bindings: object) -> tuple[bool, str]:
        token = bindings.get("signature")
        if not isinstance(token, str) or ":" not in token:
            return False, "speech_evidence_signature_invalid"
        grant_id, tag = token.rsplit(":", 1)
        if len(tag) != 64:
            return False, "speech_evidence_signature_invalid"
        grant = self._lookup(grant_id)
        if grant is None or getattr(grant, "capability", None) != "evidence_transfer":
            return False, "speech_evidence_transfer_grant_missing"
        session_id = str(bindings["session_id"])
        session_epoch = int(bindings["session_epoch"])
        if self._epochs.current_epoch("session", session_id) != session_epoch:
            return False, "speech_evidence_epoch_stale"
        expected = hmac.new(
            str(getattr(grant, "signature", "")).encode(),
            str(bindings["evidence_digest"]).encode(),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, tag):
            return False, "speech_evidence_signature_invalid"
        return self._permissions.evaluate(
            grant,
            tenant_id=str(bindings["tenant_id"]),
            subject_id=str(bindings["peer_id"]),
            scope_kind="session",
            scope_id=session_id,
            direction="ingress",
            data_type=str(bindings["data_class"]),
            purpose=str(bindings["purpose"]),
            epoch=session_epoch,
        )


@dataclass(frozen=True)
class SpeechEvidenceAdmissionDecision:
    admission_digest: str
    decision: str
    reason_codes: tuple[str, ...]
    metrics: Mapping[str, float | int]
    policy_version: str

    def public_dict(self) -> dict[str, object]:
        return {
            "admission_digest": self.admission_digest,
            "decision": self.decision,
            "reason_codes": list(self.reason_codes),
            "metrics": dict(self.metrics),
            "policy_version": self.policy_version,
        }


class SpeechEvidenceAdmissionPolicy:
    VERSION = "speech-evidence-admission-v1"
    MAX_SCAN_TEXT_CHARS = 200_000

    def __init__(
        self,
        *,
        authority: SpeechEvidenceAuthorityPort | None = None,
        evidence: SpeechEvidenceRepository | None = None,
        consent: SpeechEvidenceConsentService | None = None,
        encryption: SpeechEvidenceEncryptionPort | None = None,
        quality: SpeechEvidenceQualityPolicy | None = None,
        audit: SemanticMediaAuditPort | None = None,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self._authority = authority or UnavailableSpeechEvidenceAuthority()
        self._evidence = evidence or get_speech_evidence_repository()
        self._consent = consent or get_speech_evidence_consent_service()
        self._encryption = encryption or get_speech_evidence_encryption_port()
        self._quality = quality or SpeechEvidenceQualityPolicy()
        self._audit = audit
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)

    def admit(
        self,
        principal: VoicePrincipal,
        evidence_id: str,
        *,
        peer_id: str,
        speaker_id: str,
        recipient_id: str,
        direction: str,
        data_class: str,
        purpose: str,
        evidence_signature: str,
        provenance_digest: str,
        source_digest: str,
        speaker_scope_digest: str,
        transcript_authority: str,
        quality_metrics: Mapping[str, object],
        security_mode: str = "trusted_compute",
        external_quarantine_reasons: tuple[str, ...] = (),
        external_reject_reasons: tuple[str, ...] = (),
    ) -> SpeechEvidenceAdmissionDecision:
        record = self._evidence.get(
            tenant_id=principal.tenant_id, owner_subject=principal.subject, evidence_id=evidence_id
        )
        if record is None:
            return self._decision("rejected", ("speech_evidence_not_found",), {}, evidence_id)
        existing = self._existing(principal, record.evidence_id)
        hard_reasons: list[str] = []
        quarantine_reasons: list[str] = []
        hard_reasons.extend(_policy_reasons(external_reject_reasons))
        quarantine_reasons.extend(_policy_reasons(external_quarantine_reasons))
        if existing is None and record.state != "quarantined":
            hard_reasons.append("speech_evidence_state_invalid")
        if record.provenance_digest != provenance_digest:
            hard_reasons.append("speech_evidence_provenance_mismatch")
        if record.source_digest != source_digest:
            hard_reasons.append("speech_evidence_source_mismatch")
        if record.speaker_scope_digest != speaker_scope_digest:
            hard_reasons.append("speech_evidence_speaker_scope_mismatch")
        if transcript_authority not in {"human_verified", "hub_fusion_verified", "source_authoritative"}:
            hard_reasons.append("speech_evidence_transcript_authority_invalid")
        try:
            consent = self._consent.authorize_claim(
                principal,
                record.consent_id,
                expected_consent_version=record.consent_version,
                expected_revocation_epoch=record.revocation_epoch,
                expected_consent_digest=self._consent.get(principal, record.consent_id).consent_digest,
                grant=_grant_for(record.evidence_class),
                speaker_id=speaker_id,
                recipient_id=recipient_id,
                direction=direction,
                pair_id=record.pair_id,
                session_id=record.session_id,
                session_epoch=record.session_epoch,
                purpose=purpose,
                data_class=data_class,
            )
        except Exception as exc:
            consent = None
            hard_reasons.append(str(getattr(exc, "reason_code", "speech_evidence_consent_invalid")))
        authority_digest = hashlib.sha256(
            canonical_json(
                {
                    "content_digest": record.content_digest,
                    "provenance_digest": provenance_digest,
                    "source_digest": source_digest,
                    "speaker_scope_digest": speaker_scope_digest,
                    "transcript_authority": transcript_authority,
                }
            )
        ).hexdigest()
        allowed, authority_reason = self._authority.authorize(
            tenant_id=principal.tenant_id,
            peer_id=peer_id,
            pair_id=record.pair_id,
            session_id=record.session_id,
            session_epoch=record.session_epoch,
            direction=direction,
            data_class=data_class,
            purpose=purpose,
            evidence_digest=authority_digest,
            signature=evidence_signature,
        )
        if not allowed:
            hard_reasons.append(authority_reason)
        try:
            quality = self._quality.evaluate(quality_metrics)
            metrics = quality.normalized_metrics
            quarantine_reasons.extend(quality.reason_codes)
        except SpeechEvidenceQualityError as exc:
            metrics = {}
            hard_reasons.append(exc.reason_code)
        if consent is not None and record.expires_at_ms > consent.expires_at_ms:
            hard_reasons.append("speech_evidence_retention_exceeds_consent")
        if not hard_reasons:
            try:
                envelope = self._evidence.encrypted(
                    tenant_id=principal.tenant_id,
                    owner_subject=principal.subject,
                    evidence_id=evidence_id,
                )
                plaintext = self._encryption.decrypt(envelope, security_mode=security_mode)
                if record.evidence_class in {"transcript", "correction"}:
                    findings = self._scan_text(plaintext)
                    quarantine_reasons.extend(findings)
            except Exception as exc:
                hard_reasons.append(str(getattr(exc, "reason_code", "speech_evidence_decryption_failed")))
        if hard_reasons:
            decision = "rejected"
            reasons = tuple(sorted(set(hard_reasons)))
        elif quarantine_reasons:
            decision = "quarantined"
            reasons = tuple(sorted(set(quarantine_reasons)))
        else:
            decision = "admitted"
            reasons = ("speech_evidence_admitted",)
        outcome = self._decision(decision, reasons, metrics, record.content_digest)
        if existing is not None:
            if existing.admission_digest != outcome.admission_digest:
                return self._decision(
                    "rejected",
                    ("speech_evidence_admission_replay_mismatch",),
                    {},
                    record.content_digest,
                )
            self._repair_evidence_projection(principal, record, existing)
            return existing
        row = SpeechEvidenceAdmissionDB(
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            evidence_id=record.evidence_id,
            evidence_digest=record.content_digest,
            admission_digest=outcome.admission_digest,
            policy_version=self.VERSION,
            decision=outcome.decision,
            reason_codes=list(outcome.reason_codes),
            metrics=dict(outcome.metrics),
            consent_version=record.consent_version,
            revocation_epoch=record.revocation_epoch,
            created_at_ms=self._clock_ms(),
        )
        target = "admitted" if decision == "admitted" else decision
        audit_event = self._prepare_audit(principal, record, outcome, target)
        try:
            with Session(engine) as session:
                session.add(row)
                projection = session.exec(
                    update(SpeechEvidenceDB)
                    .where(
                        SpeechEvidenceDB.id == record.evidence_id,
                        SpeechEvidenceDB.tenant_id == principal.tenant_id,
                        SpeechEvidenceDB.owner_subject == principal.subject,
                        SpeechEvidenceDB.state == "quarantined",
                    )
                    .values(
                        state=target,
                        admission_digest=outcome.admission_digest,
                        updated_at_ms=self._clock_ms(),
                    )
                )
                if projection.rowcount != 1:
                    session.rollback()
                    raise RuntimeError("speech_evidence_admission_projection_conflict")
                if audit_event is not None:
                    SqlSemanticMediaAuditOutbox.enqueue_in_session(session, audit_event)
                session.commit()
        except IntegrityError:
            replay = self._existing(principal, record.evidence_id)
            if replay is not None:
                if replay.admission_digest != outcome.admission_digest:
                    return self._decision(
                        "rejected",
                        ("speech_evidence_admission_replay_mismatch",),
                        {},
                        record.content_digest,
                    )
                self._repair_evidence_projection(principal, record, replay)
                return replay
            raise
        return outcome

    def _scan_text(self, payload: bytes) -> list[str]:
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            return ["speech_evidence_transcript_encoding_invalid"]
        if len(text) > self.MAX_SCAN_TEXT_CHARS:
            return ["speech_evidence_privacy_scan_limit_exceeded"]
        reasons: list[str] = []
        if _EMAIL.search(text) or _PHONE.search(text):
            reasons.append("speech_evidence_pii_detected")
        if _SECRET.search(text):
            reasons.append("speech_evidence_secret_detected")
        if _PROMPT_INJECTION.search(text):
            reasons.append("speech_evidence_prompt_injection_detected")
        return reasons

    def _existing(
        self,
        principal: VoicePrincipal,
        evidence_id: str,
    ) -> SpeechEvidenceAdmissionDecision | None:
        with Session(engine) as session:
            row = session.exec(
                select(SpeechEvidenceAdmissionDB).where(
                    SpeechEvidenceAdmissionDB.evidence_id == evidence_id,
                    SpeechEvidenceAdmissionDB.tenant_id == principal.tenant_id,
                    SpeechEvidenceAdmissionDB.owner_subject == principal.subject,
                    SpeechEvidenceAdmissionDB.policy_version == self.VERSION,
                )
            ).first()
            if row is None:
                return None
            return SpeechEvidenceAdmissionDecision(
                admission_digest=row.admission_digest,
                decision=row.decision,
                reason_codes=tuple(row.reason_codes or []),
                metrics=dict(row.metrics or {}),
                policy_version=row.policy_version,
            )

    def _repair_evidence_projection(
        self,
        principal: VoicePrincipal,
        record: SpeechEvidenceRecord,
        outcome: SpeechEvidenceAdmissionDecision,
    ) -> None:
        """Finish an admission replay interrupted after its decision commit.

        The durable admission row is the decision record.  Updating the
        evidence projection is intentionally replayable so a crash between
        those two commits cannot strand admitted evidence in quarantine.
        """

        if record.state != "quarantined":
            return
        target = "admitted" if outcome.decision == "admitted" else outcome.decision
        self._evidence.transition(
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            evidence_id=record.evidence_id,
            expected_states=("quarantined",),
            target=target,
            now_ms=self._clock_ms(),
            admission_digest=outcome.admission_digest,
            audit_event=self._prepare_audit(principal, record, outcome, target),
        )

    def _prepare_audit(
        self,
        principal: VoicePrincipal,
        record: SpeechEvidenceRecord,
        outcome: SpeechEvidenceAdmissionDecision,
        target: str,
    ) -> SemanticMediaAuditEvent | None:
        if self._audit is None:
            return None
        try:
            return self._audit.prepare_transition(
                idempotency_key=(
                    f"speech-evidence:admission:{record.evidence_id}:"
                    f"{outcome.admission_digest}"
                ),
                tenant_id=principal.tenant_id,
                scope=f"speech-evidence:{record.session_id}",
                event_type="speech_evidence",
                transition=target,
                reason_code=f"speech_evidence_{target}",
                epoch=record.session_epoch,
                job_ref=record.evidence_id,
            )
        except Exception as exc:
            raise RuntimeError("speech_evidence_admission_audit_unavailable") from exc

    def _decision(
        self,
        decision: str,
        reasons: tuple[str, ...],
        metrics: Mapping[str, float | int],
        binding: str,
    ) -> SpeechEvidenceAdmissionDecision:
        digest = hashlib.sha256(
            canonical_json(
                {
                    "policy_version": self.VERSION,
                    "binding": binding,
                    "decision": decision,
                    "reason_codes": list(reasons),
                    "metrics": dict(metrics),
                }
            )
        ).hexdigest()
        return SpeechEvidenceAdmissionDecision(digest, decision, reasons, dict(metrics), self.VERSION)


def _grant_for(evidence_class: str) -> str:
    return {
        "audio": "raw_audio_share",
        "transcript": "transcript_share",
        "correction": "transcript_share",
        "acoustic_features": "feature_share",
        "speaker_embedding": "feature_share",
        "quality_metrics": "feature_share",
    }.get(evidence_class, "capture")


def _policy_reasons(values: tuple[str, ...]) -> tuple[str, ...]:
    """Accept only bounded reason codes from Hub-owned pre-admission gates."""

    if len(values) > 32:
        return ("speech_evidence_external_policy_invalid",)
    normalized = tuple(sorted(set(str(value) for value in values)))
    if any(
        not value.startswith("speech_evidence_")
        or len(value) > 160
        or not value.replace("_", "").isalnum()
        for value in normalized
    ):
        return ("speech_evidence_external_policy_invalid",)
    return normalized


__all__ = [
    "SpeechEvidenceAdmissionDecision",
    "SpeechEvidenceAdmissionPolicy",
    "SpeechEvidenceAuthorityPort",
    "M1SpeechEvidenceAuthority",
    "UnavailableSpeechEvidenceAuthority",
]
