"""Hub-owned state machine for purpose-bound speech-evidence consent."""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Callable, Protocol

from agent.repositories.speech_consent_repository import (
    SpeechConsentRepository,
    SpeechConsentRepositoryError,
    get_speech_consent_repository,
)
from agent.services.semantic_media_audit_service import SemanticMediaAuditEvent
from agent.services.voice_governance_domain import VoicePrincipal
from ananta_contracts.speech_evidence_governance import (
    SPEECH_GRANTS,
    SpeechEvidenceConsent,
    SpeechEvidenceGovernanceError,
)


class SpeechEvidenceConsentService:
    """Own consent decisions; callers can request scope but cannot self-authorize."""

    def __init__(
        self,
        repository: SpeechConsentRepository | None = None,
        *,
        clock_ms: Callable[[], int] | None = None,
        audit: "SpeechConsentAuditPort | None" = None,
    ) -> None:
        self._repository = repository or get_speech_consent_repository()
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._audit = audit

    def grant(
        self,
        principal: VoicePrincipal,
        raw: object,
        *,
        authority: str = "hub",
    ) -> SpeechEvidenceConsent:
        self._require_hub(authority)
        consent = SpeechEvidenceConsent.from_mapping(raw, now_ms=self._clock_ms())
        self._require_principal(principal, consent)
        if consent.state != "active" or consent.consent_version != 1 or consent.revocation_epoch != 0:
            raise SpeechEvidenceGovernanceError(
                "speech_consent_initial_state_invalid",
                "new grants must start active at consent version 1 and revocation epoch 0",
            )
        existing = self._repository.get(
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            consent_id=consent.consent_id,
        )
        if existing is not None:
            if existing.consent_digest == consent.consent_digest:
                return existing
            raise SpeechEvidenceGovernanceError(
                "speech_consent_id_conflict", "consent ID is already bound", status_code=409
            )
        try:
            result = self._repository.create(
                consent,
                audit_event=self._prepare_audit_transition(principal, consent, "granted"),
            )
        except SpeechConsentRepositoryError as exc:
            raise SpeechEvidenceGovernanceError(exc.reason_code, str(exc), status_code=409) from exc
        return result

    def reduce(
        self,
        principal: VoicePrincipal,
        raw: object,
        *,
        expected_version: int,
        authority: str = "hub",
    ) -> SpeechEvidenceConsent:
        self._require_hub(authority)
        candidate = SpeechEvidenceConsent.from_mapping(raw, now_ms=self._clock_ms())
        current = self._current(principal, candidate.consent_id)
        self._require_principal(principal, candidate)
        if self._is_retry(current, candidate, expected_version):
            return current
        self._expected(current, expected_version)
        if not candidate.is_scope_subset_of(current) or candidate.expires_at_ms > current.expires_at_ms:
            raise SpeechEvidenceGovernanceError(
                "speech_consent_scope_expansion_requires_new_grant",
                "reduce may only preserve or attenuate the current scope",
                status_code=403,
            )
        next_consent = replace(
            candidate,
            consent_version=current.consent_version + 1,
            revocation_epoch=current.revocation_epoch + 1,
            issued_at_ms=current.issued_at_ms,
            state="active",
        )
        result = self._replace(principal, current, next_consent, transition="reduced")
        return result

    def renew(
        self,
        principal: VoicePrincipal,
        raw: object,
        *,
        expected_version: int,
        authority: str = "hub",
    ) -> SpeechEvidenceConsent:
        self._require_hub(authority)
        candidate = SpeechEvidenceConsent.from_mapping(raw, now_ms=self._clock_ms())
        current = self._current(principal, candidate.consent_id)
        self._require_principal(principal, candidate)
        if self._is_retry(current, candidate, expected_version):
            return current
        self._expected(current, expected_version)
        if current.state != "active":
            raise SpeechEvidenceGovernanceError(
                "speech_consent_renew_inactive",
                "revoked or expired consent needs a new explicit grant",
                status_code=409,
            )
        if not candidate.is_scope_subset_of(current):
            raise SpeechEvidenceGovernanceError(
                "speech_consent_scope_expansion_requires_new_grant",
                "renew may only preserve or attenuate the current scope",
                status_code=403,
            )
        reduced = candidate.scope_digest != current.scope_digest
        next_consent = replace(
            candidate,
            consent_version=current.consent_version + 1,
            revocation_epoch=current.revocation_epoch + (1 if reduced else 0),
            issued_at_ms=self._clock_ms(),
            state="active",
        )
        result = self._replace(principal, current, next_consent, transition="renewed")
        return result

    def revoke(
        self,
        principal: VoicePrincipal,
        consent_id: str,
        *,
        expected_version: int,
        contributor_id: str | None = None,
        authority: str = "hub",
    ) -> SpeechEvidenceConsent:
        self._require_hub(authority)
        current = self._current(principal, consent_id)
        if contributor_id is not None and contributor_id not in set(current.required_signers):
            raise SpeechEvidenceGovernanceError(
                "speech_consent_revoker_not_contributor",
                "only a bound contributor may revoke bilateral evidence",
                status_code=403,
            )
        if (
            type(expected_version) is int
            and current.state == "revoked"
            and current.consent_version
            in {
                expected_version,
                expected_version + 1,
            }
        ):
            return current
        self._expected(current, expected_version)
        next_consent = replace(
            current,
            grant_items=tuple((name, False) for name in SPEECH_GRANTS),
            state="revoked",
            consent_version=current.consent_version + 1,
            revocation_epoch=current.revocation_epoch + 1,
        )
        result = self._replace(principal, current, next_consent, transition="revoked")
        return result

    def expire(
        self,
        principal: VoicePrincipal,
        consent_id: str,
        *,
        expected_version: int,
        authority: str = "hub",
    ) -> SpeechEvidenceConsent:
        self._require_hub(authority)
        current = self._current(principal, consent_id)
        if (
            type(expected_version) is int
            and current.state == "expired"
            and current.consent_version
            in {
                expected_version,
                expected_version + 1,
            }
        ):
            return current
        self._expected(current, expected_version)
        if self._clock_ms() < current.expires_at_ms:
            raise SpeechEvidenceGovernanceError(
                "speech_consent_not_due", "consent has not reached expiry", status_code=409
            )
        next_consent = replace(
            current,
            grant_items=tuple((name, False) for name in SPEECH_GRANTS),
            state="expired",
            consent_version=current.consent_version + 1,
            revocation_epoch=current.revocation_epoch + 1,
        )
        result = self._replace(principal, current, next_consent, transition="expired")
        return result

    def authorize_claim(
        self,
        principal: VoicePrincipal,
        consent_id: str,
        *,
        expected_consent_version: int,
        expected_revocation_epoch: int,
        expected_consent_digest: str,
        grant: str,
        speaker_id: str,
        recipient_id: str,
        direction: str,
        pair_id: str,
        session_id: str,
        session_epoch: int,
        purpose: str,
        data_class: str,
        trainer_location: str | None = None,
    ) -> SpeechEvidenceConsent:
        current = self._current(principal, consent_id)
        if (
            current.consent_version != expected_consent_version
            or current.revocation_epoch != expected_revocation_epoch
            or current.consent_digest != expected_consent_digest
        ):
            raise SpeechEvidenceGovernanceError(
                "speech_consent_stale_claim",
                "worker, cache or client claim is stale",
                status_code=409,
            )
        current.allows(
            grant,
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            speaker_id=speaker_id,
            recipient_id=recipient_id,
            direction=direction,
            pair_id=pair_id,
            session_id=session_id,
            session_epoch=session_epoch,
            purpose=purpose,
            data_class=data_class,
            trainer_location=trainer_location,
            now_ms=self._clock_ms(),
        )
        return current

    def get(self, principal: VoicePrincipal, consent_id: str) -> SpeechEvidenceConsent:
        current = self._repository.get_for_participant(
            tenant_id=principal.tenant_id,
            participant_id=principal.subject,
            consent_id=consent_id,
        )
        if current is None:
            raise SpeechEvidenceGovernanceError(
                "speech_consent_not_found", "speech consent was not found", status_code=404
            )
        return current

    @staticmethod
    def _require_hub(authority: str) -> None:
        if authority != "hub":
            raise SpeechEvidenceGovernanceError(
                "speech_consent_hub_authority_required",
                "only the Hub may mutate speech consent",
                status_code=403,
            )

    @staticmethod
    def _require_principal(principal: VoicePrincipal, consent: SpeechEvidenceConsent) -> None:
        if consent.tenant_id != principal.tenant_id or consent.owner_subject != principal.subject:
            raise SpeechEvidenceGovernanceError(
                "speech_consent_principal_mismatch", "tenant or owner does not match", status_code=403
            )

    @staticmethod
    def _expected(current: SpeechEvidenceConsent, expected_version: int) -> None:
        if isinstance(expected_version, bool) or expected_version != current.consent_version:
            raise SpeechEvidenceGovernanceError(
                "speech_consent_stale_version", "consent version does not match", status_code=409
            )

    def _current(self, principal: VoicePrincipal, consent_id: str) -> SpeechEvidenceConsent:
        current = self._repository.get(
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            consent_id=consent_id,
        )
        if current is None:
            raise SpeechEvidenceGovernanceError(
                "speech_consent_not_found", "speech consent was not found", status_code=404
            )
        return current

    def _replace(
        self,
        principal: VoicePrincipal,
        current: SpeechEvidenceConsent,
        next_consent: SpeechEvidenceConsent,
        *,
        transition: str,
    ) -> SpeechEvidenceConsent:
        try:
            return self._repository.replace(
                next_consent,
                expected_version=current.consent_version,
                expected_revocation_epoch=current.revocation_epoch,
                audit_event=self._prepare_audit_transition(
                    principal,
                    next_consent,
                    transition,
                ),
            )
        except SpeechConsentRepositoryError as exc:
            raise SpeechEvidenceGovernanceError(exc.reason_code, str(exc), status_code=409) from exc

    def _prepare_audit_transition(
        self,
        principal: VoicePrincipal,
        consent: SpeechEvidenceConsent,
        transition: str,
    ) -> SemanticMediaAuditEvent | None:
        if self._audit is None:
            return None
        try:
            return self._audit.prepare_transition(
                idempotency_key=(f"speech-consent:{transition}:{consent.consent_id}:{consent.consent_version}"),
                tenant_id=principal.tenant_id,
                scope=f"speech-consent:{consent.pair_id}:{consent.direction}",
                event_type="semantic_consent",
                transition=transition,
                reason_code="hub_confirmed",
                epoch=consent.consent_version,
                contract_ref=consent.consent_digest,
            )
        except Exception as exc:
            raise SpeechEvidenceGovernanceError(
                "speech_consent_audit_unavailable",
                "the durable consent audit is unavailable",
                status_code=503,
            ) from exc

    @staticmethod
    def _is_retry(
        current: SpeechEvidenceConsent,
        candidate: SpeechEvidenceConsent,
        expected_version: int,
    ) -> bool:
        return (
            type(expected_version) is int
            and current.state == "active"
            and current.consent_version == expected_version + 1
            and current.scope_digest == candidate.scope_digest
            and current.expires_at_ms == candidate.expires_at_ms
            and current.required_signers == candidate.required_signers
            and current.signature_items == candidate.signature_items
        )


class SpeechConsentAuditPort(Protocol):
    def prepare_transition(self, **kwargs) -> SemanticMediaAuditEvent: ...


_service = SpeechEvidenceConsentService()


def get_speech_evidence_consent_service() -> SpeechEvidenceConsentService:
    try:
        from flask import current_app, has_app_context

        if has_app_context():
            configured = current_app.extensions.get("speech_evidence_consent_service")
            if isinstance(configured, SpeechEvidenceConsentService):
                return configured
            configured = SpeechEvidenceConsentService(audit=current_app.extensions.get("semantic_media_audit_recorder"))
            current_app.extensions["speech_evidence_consent_service"] = configured
            return configured
    except RuntimeError:
        pass
    return _service


__all__ = [
    "SpeechConsentAuditPort",
    "SpeechEvidenceConsentService",
    "get_speech_evidence_consent_service",
]
