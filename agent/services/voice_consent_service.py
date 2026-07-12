from __future__ import annotations

from typing import Any, Callable

from agent.common.audit import log_audit
from agent.db_models import VoiceConsentDB
from agent.repositories.voice_governance import VoiceConsentRepository
from agent.services.voice_governance_domain import VoiceGovernanceError, VoicePrincipal, validate_identifier
from agent.services.voice_idempotency_service import VoiceIdempotencyService
from agent.services.voice_retention_policy import voice_retention_policy

ALLOWED_CONSENT_CATEGORIES = {
    "audio_fingerprint",
    "preferences",
    "text_corrections",
    "vocabulary",
}


class VoiceConsentService:
    def __init__(
        self,
        repository: VoiceConsentRepository | None = None,
        idempotency: VoiceIdempotencyService | None = None,
        audit_sink: Callable[[str, dict], None] = log_audit,
    ) -> None:
        self._repository = repository or VoiceConsentRepository()
        self._idempotency = idempotency or VoiceIdempotencyService()
        self._audit = audit_sink

    def get(self, principal: VoicePrincipal, profile_id: str) -> dict[str, Any]:
        normalized_profile_id = validate_identifier(profile_id, field="profile_id")
        consent = self._repository.get(principal, normalized_profile_id)
        return self._public(consent, profile_id=normalized_profile_id)

    def require_active(
        self,
        principal: VoicePrincipal,
        profile_id: str,
        *,
        category: str | None = None,
    ) -> VoiceConsentDB:
        normalized_profile_id = validate_identifier(profile_id, field="profile_id")
        consent = self._repository.get(principal, normalized_profile_id)
        if consent is None or not consent.granted:
            raise VoiceGovernanceError(
                code="voice_consent.required",
                message="active voice personalization consent is required",
                status_code=403,
            )
        if category is not None and category not in set(consent.categories or []):
            raise VoiceGovernanceError(
                code="voice_consent.category_not_granted",
                message=f"consent does not include category {category}",
                status_code=403,
            )
        return consent

    def set(
        self,
        principal: VoicePrincipal,
        *,
        profile_id: str,
        granted: bool,
        categories: list[str],
        retention_days: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        normalized_profile_id = validate_identifier(profile_id, field="profile_id")
        normalized_categories = self._normalize_categories(categories if granted else [])
        if not isinstance(retention_days, int) or isinstance(retention_days, bool) or not 1 <= retention_days <= 3650:
            raise VoiceGovernanceError(
                code="voice_consent.invalid_retention",
                message="retention_days must be an integer between 1 and 3650",
                status_code=422,
            )
        payload = {
            "profile_id": normalized_profile_id,
            "granted": bool(granted),
            "categories": normalized_categories,
            "retention_days": retention_days,
        }
        claim = self._idempotency.begin(
            principal,
            operation=f"voice_consent.set:{normalized_profile_id}",
            idempotency_key=idempotency_key,
            payload=payload,
        )
        if claim.replayed:
            return {**dict(claim.result_metadata.get("consent") or {}), "idempotent_replay": True}
        if claim.lease_token is None:
            raise RuntimeError("active consent idempotency claim has no lease token")
        try:
            consent, result = self._repository.set_state(
                principal,
                profile_id=normalized_profile_id,
                granted=bool(granted),
                categories=normalized_categories,
                retention_days=retention_days,
                idempotency_record_id=claim.record_id,
                idempotency_lease_token=claim.lease_token,
                result_builder=lambda value: self._public(
                    value,
                    profile_id=normalized_profile_id,
                ),
            )
        except Exception:
            self._idempotency.abandon(claim)
            raise
        self._audit(
            "voice_consent_updated",
            {
                "tenant_id": principal.tenant_id,
                "owner_subject": principal.subject,
                "profile_id": normalized_profile_id,
                "consent_id": consent.id,
                "consent_version": consent.version,
                "granted": consent.granted,
                "categories": list(consent.categories or []),
            },
        )
        return {**result, "idempotent_replay": False}

    @staticmethod
    def _normalize_categories(categories: list[str]) -> list[str]:
        if not isinstance(categories, list):
            raise VoiceGovernanceError(
                code="voice_consent.invalid_categories",
                message="categories must be a list",
                status_code=422,
            )
        normalized = sorted({str(item or "").strip() for item in categories if str(item or "").strip()})
        unknown = sorted(set(normalized) - ALLOWED_CONSENT_CATEGORIES)
        if unknown:
            raise VoiceGovernanceError(
                code="voice_consent.invalid_categories",
                message=f"unsupported consent categories: {', '.join(unknown)}",
                status_code=422,
            )
        return normalized

    @staticmethod
    def _public(consent: VoiceConsentDB | None, *, profile_id: str) -> dict[str, Any]:
        if consent is None:
            return {
                "id": None,
                "profile_id": profile_id,
                "granted": False,
                "categories": [],
                "retention_days": None,
                "version": 0,
                "granted_at": None,
                "revoked_at": None,
                "retention_policy": voice_retention_policy(categories=[], retention_days=None),
            }
        return {
            "id": consent.id,
            "profile_id": consent.profile_id,
            "granted": bool(consent.granted),
            "categories": list(consent.categories or []),
            "retention_days": int(consent.retention_days),
            "version": int(consent.version),
            "granted_at": consent.granted_at,
            "revoked_at": consent.revoked_at,
            "retention_policy": voice_retention_policy(
                categories=list(consent.categories or []),
                retention_days=consent.retention_days,
            ),
        }


voice_consent_service = VoiceConsentService()


def get_voice_consent_service() -> VoiceConsentService:
    return voice_consent_service
