from __future__ import annotations

import hashlib
import threading
import time
from typing import Any, Callable

from agent.common.audit import log_audit
from agent.db_models import VoiceFeedbackDB
from agent.repositories.voice_governance import VoicePersonalizationRepository
from agent.services.voice_consent_service import VoiceConsentService, get_voice_consent_service
from agent.services.voice_governance_domain import (
    VoiceGovernanceError,
    VoicePrincipal,
    stable_payload_hash,
    validate_identifier,
    validate_text,
)
from agent.services.voice_idempotency_service import VoiceIdempotencyService
from agent.services.voice_review_service import VoiceReviewService, get_voice_review_service
from agent.services.voice_sensitive_text_codec import VoiceSensitiveTextCodec, get_voice_sensitive_text_codec

_KIND_CATEGORY = {
    "negative": "text_corrections",
    "preference": "preferences",
    "substitution": "text_corrections",
    "vocabulary": "vocabulary",
}
_SAFE_METADATA_KEYS = {"backend", "domain", "language", "model_revision", "reason_code"}
_PERSONALIZATION_RESET_LOCKS = tuple(threading.RLock() for _index in range(64))


class VoicePersonalizationService:
    def __init__(
        self,
        repository: VoicePersonalizationRepository | None = None,
        consent_service: VoiceConsentService | None = None,
        review_service: VoiceReviewService | None = None,
        idempotency: VoiceIdempotencyService | None = None,
        text_codec: VoiceSensitiveTextCodec | None = None,
        audit_sink: Callable[[str, dict], None] = log_audit,
    ) -> None:
        self._repository = repository or VoicePersonalizationRepository()
        self._consent = consent_service or get_voice_consent_service()
        self._reviews = review_service or get_voice_review_service()
        self._idempotency = idempotency or VoiceIdempotencyService()
        self._text_codec = text_codec or get_voice_sensitive_text_codec()
        self._audit = audit_sink

    def add_feedback(
        self,
        principal: VoicePrincipal,
        *,
        profile_id: str,
        review_id: str,
        kind: str,
        source_text: str | None,
        target_text: str | None,
        metadata: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        normalized_profile_id = validate_identifier(profile_id, field="profile_id")
        normalized_review_id = validate_identifier(review_id, field="review_id")
        normalized_kind = str(kind or "").strip().lower()
        category = _KIND_CATEGORY.get(normalized_kind)
        if category is None:
            raise VoiceGovernanceError(
                code="voice_personalization.invalid_kind",
                message="kind must be negative, preference, substitution or vocabulary",
                status_code=422,
            )
        normalized_source = validate_text(source_text, field="source_text", max_length=4000)
        normalized_target = validate_text(target_text, field="target_text", max_length=4000)
        self._validate_kind_text(normalized_kind, normalized_source, normalized_target)
        normalized_metadata = self._normalize_metadata(metadata)
        payload = {
            "profile_id": normalized_profile_id,
            "review_id": normalized_review_id,
            "kind": normalized_kind,
            "source_text": normalized_source,
            "target_text": normalized_target,
            "metadata": normalized_metadata,
        }
        claim = self._idempotency.begin(
            principal,
            operation=f"voice_personalization.feedback:{normalized_profile_id}",
            idempotency_key=idempotency_key,
            payload=payload,
        )
        if claim.replayed:
            feedback_id = str(claim.result_metadata.get("feedback_id") or "")
            feedback = self._repository.get_feedback(principal, feedback_id)
            if feedback is None:
                raise VoiceGovernanceError(
                    code="voice_personalization.replayed_resource_deleted",
                    message="the idempotent feedback resource was deleted",
                    status_code=410,
                )
            return {**self._public_feedback(feedback), "idempotent_replay": True}
        try:
            consent = self._consent.require_active(
                principal,
                normalized_profile_id,
                category=category,
            )
            self._reviews.require_for_feedback(
                principal,
                normalized_review_id,
                profile_id=normalized_profile_id,
            )
            feedback, profile = self._repository.create_feedback(
                principal,
                profile_id=normalized_profile_id,
                consent_id=consent.id,
                consent_version=int(consent.version),
                source_review_id=normalized_review_id,
                kind=normalized_kind,
                source_ciphertext=self._text_codec.encrypt(normalized_source),
                target_ciphertext=self._text_codec.encrypt(normalized_target),
                feedback_metadata=normalized_metadata,
            )
            self._idempotency.complete(
                claim,
                {"feedback_id": feedback.id, "profile_version": profile.version},
            )
        except Exception:
            self._idempotency.abandon(claim)
            raise
        self._audit(
            "voice_personalization_feedback_added",
            {
                "tenant_id": principal.tenant_id,
                "owner_subject": principal.subject,
                "profile_id": normalized_profile_id,
                "feedback_id": feedback.id,
                "review_id": normalized_review_id,
                "kind": normalized_kind,
                "consent_id": consent.id,
                "consent_version": consent.version,
                "profile_version": profile.version,
            },
        )
        return {**self._public_feedback(feedback), "profile_version": profile.version, "idempotent_replay": False}

    def export(self, principal: VoicePrincipal, profile_id: str) -> dict[str, Any]:
        normalized_profile_id = validate_identifier(profile_id, field="profile_id")
        feedback = self._repository.list_feedback(principal, normalized_profile_id)
        return {
            "schema_version": "voice-personalization.v1",
            "profile_id": normalized_profile_id,
            "version": self._repository.profile_version(principal, normalized_profile_id),
            "items": [self._public_feedback(item) for item in feedback],
        }

    def import_payload(
        self,
        principal: VoicePrincipal,
        *,
        profile_id: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        normalized_profile_id = validate_identifier(profile_id, field="profile_id")
        if not isinstance(payload, dict) or payload.get("schema_version") != "voice-personalization.v1":
            raise VoiceGovernanceError(
                code="voice_personalization.invalid_import_schema",
                message="unsupported voice personalization import schema",
                status_code=422,
            )
        if str(payload.get("profile_id") or normalized_profile_id) != normalized_profile_id:
            raise VoiceGovernanceError(
                code="voice_personalization.import_profile_mismatch",
                message="import profile does not match the target profile",
                status_code=409,
            )
        items = payload.get("items")
        if not isinstance(items, list) or len(items) > 500:
            raise VoiceGovernanceError(
                code="voice_personalization.invalid_import_items",
                message="import items must be an array with at most 500 entries",
                status_code=422,
            )
        normalized_items: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                raise VoiceGovernanceError(
                    code="voice_personalization.invalid_import_item",
                    message="every import item must be an object",
                    status_code=422,
                )
            kind = str(item.get("kind") or "").strip().lower()
            if kind not in _KIND_CATEGORY:
                raise VoiceGovernanceError(
                    code="voice_personalization.invalid_kind",
                    message="unsupported voice personalization feedback kind",
                    status_code=422,
                )
            source = validate_text(item.get("source_text"), field="source_text", max_length=4000)
            target = validate_text(item.get("target_text"), field="target_text", max_length=4000)
            self._validate_kind_text(kind, source, target)
            normalized_items.append(
                {
                    "kind": kind,
                    "source_text": source,
                    "target_text": target,
                    "metadata": self._normalize_metadata(item.get("metadata") or {}),
                }
            )
        consent = self._consent.require_active(principal, normalized_profile_id)
        required_categories = {_KIND_CATEGORY[item["kind"]] for item in normalized_items}
        missing_categories = required_categories - set(consent.categories or [])
        if missing_categories:
            raise VoiceGovernanceError(
                code="voice_consent.category_not_granted",
                message="voice personalization consent does not grant all imported categories",
                status_code=403,
            )
        claim = self._idempotency.begin(
            principal,
            operation=f"voice_personalization.import:{normalized_profile_id}",
            idempotency_key=idempotency_key,
            payload={"profile_id": normalized_profile_id, "items": normalized_items},
        )
        if claim.replayed:
            return {**claim.result_metadata, "idempotent_replay": True}
        profile_version = self._repository.profile_version(principal, normalized_profile_id)
        try:
            encrypted_items = [
                {
                    "source_review_id": f"import-{stable_payload_hash(item)[:24]}-{index}",
                    "kind": item["kind"],
                    "source_ciphertext": self._text_codec.encrypt(item["source_text"]),
                    "target_ciphertext": self._text_codec.encrypt(item["target_text"]),
                    "feedback_metadata": {**item["metadata"], "origin": "explicit_import"},
                }
                for index, item in enumerate(normalized_items)
            ]
            if encrypted_items:
                _feedback, profile = self._repository.create_feedback_many(
                    principal,
                    profile_id=normalized_profile_id,
                    consent_id=consent.id,
                    consent_version=consent.version,
                    items=encrypted_items,
                )
                profile_version = profile.version
            result = {
                "profile_id": normalized_profile_id,
                "imported_count": len(normalized_items),
                "version": profile_version,
            }
            self._idempotency.complete(claim, result)
        except Exception:
            self._idempotency.abandon(claim)
            raise
        self._audit(
            "voice_personalization_imported",
            {
                "tenant_id": principal.tenant_id,
                "owner_subject": principal.subject,
                "profile_id": normalized_profile_id,
                "imported_count": len(normalized_items),
                "profile_version": profile_version,
            },
        )
        return {**result, "idempotent_replay": False}

    def snapshot(self, principal: VoicePrincipal, profile_id: str) -> dict[str, Any]:
        normalized_profile_id = validate_identifier(profile_id, field="profile_id")
        consent = self._consent.require_active(
            principal,
            normalized_profile_id,
        )
        feedback = self._repository.list_feedback(principal, normalized_profile_id)
        vocabulary: list[str] = []
        substitutions: list[dict[str, str]] = []
        preferences: list[dict[str, str]] = []
        negative: list[dict[str, str | None]] = []
        granted_categories = set(consent.categories or [])
        for item in feedback:
            if _KIND_CATEGORY.get(item.kind) not in granted_categories:
                continue
            source_text = self._text_codec.decrypt(item.source_ciphertext)
            target_text = self._text_codec.decrypt(item.target_ciphertext)
            if item.kind == "vocabulary" and target_text:
                vocabulary.append(target_text)
            elif item.kind == "substitution" and source_text and target_text:
                substitutions.append({"source": source_text, "target": target_text})
            elif item.kind == "preference" and source_text and target_text:
                preferences.append({"source": source_text, "target": target_text})
            elif item.kind == "negative" and source_text:
                negative.append({"source": source_text, "target": target_text})
        negative_sources = {str(item["source"]).casefold() for item in negative}
        negative_pairs = {
            (str(item["source"]).casefold(), str(item.get("target") or "").casefold()) for item in negative
        }
        vocabulary = [value for value in vocabulary if value.casefold() not in negative_sources]
        substitutions = [
            item
            for item in substitutions
            if item["source"].casefold() not in negative_sources
            and (item["source"].casefold(), item["target"].casefold()) not in negative_pairs
        ]
        preferences = [
            item
            for item in preferences
            if item["source"].casefold() not in negative_sources
            and (item["source"].casefold(), item["target"].casefold()) not in negative_pairs
        ]
        return {
            "schema_version": "voice-personalization-snapshot.v1",
            "profile_id": normalized_profile_id,
            "version": self._repository.profile_version(principal, normalized_profile_id),
            "consent_id": consent.id,
            "consent_version": int(consent.version),
            "consent_granted": bool(consent.granted),
            "revocation_epoch": int(consent.version),
            "expires_at": time.time() + 300,
            "vocabulary": sorted(set(vocabulary)),
            "substitutions": substitutions,
            "preferences": preferences,
            "negative_examples": negative,
            "weights": {
                "vocabulary": 1.0,
                "substitution": 1.0,
                "preference": 0.75,
            },
            "persistence_owner": "hub",
            "runtime_persistence_allowed": False,
        }

    def reset(
        self,
        principal: VoicePrincipal,
        *,
        profile_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        normalized_profile_id = validate_identifier(profile_id, field="profile_id")
        # The idempotency row is the cross-process fence. The keyed local lock
        # prevents same-profile retries from racing on SQLite's shared connection.
        lock_scope = f"{principal.tenant_id}\0{principal.subject}\0{normalized_profile_id}"
        lock_index = int(hashlib.sha256(lock_scope.encode("utf-8")).hexdigest()[:8], 16)
        with _PERSONALIZATION_RESET_LOCKS[lock_index % len(_PERSONALIZATION_RESET_LOCKS)]:
            return self._reset_locked(
                principal,
                normalized_profile_id=normalized_profile_id,
                idempotency_key=idempotency_key,
            )

    def _reset_locked(
        self,
        principal: VoicePrincipal,
        *,
        normalized_profile_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        payload = {"profile_id": normalized_profile_id}
        claim = self._idempotency.begin(
            principal,
            operation=f"voice_personalization.reset:{normalized_profile_id}",
            idempotency_key=idempotency_key,
            payload=payload,
        )
        if claim.replayed:
            return {**dict(claim.result_metadata), "idempotent_replay": True}
        if claim.lease_token is None:
            raise RuntimeError("active personalization-reset idempotency claim has no lease token")
        try:
            deleted_count, profile_version, result = self._repository.reset(
                principal,
                normalized_profile_id,
                idempotency_record_id=claim.record_id,
                idempotency_lease_token=claim.lease_token,
                result_builder=lambda count, version: {
                    "profile_id": normalized_profile_id,
                    "deleted_count": count,
                    "version": version,
                },
            )
        except Exception:
            self._idempotency.abandon(claim)
            raise
        self._audit(
            "voice_personalization_reset",
            {
                "tenant_id": principal.tenant_id,
                "owner_subject": principal.subject,
                "profile_id": normalized_profile_id,
                "deleted_count": deleted_count,
                "profile_version": profile_version,
            },
        )
        return {**result, "idempotent_replay": False}

    @staticmethod
    def _normalize_metadata(metadata: dict[str, Any]) -> dict[str, str]:
        if not isinstance(metadata, dict):
            raise VoiceGovernanceError(
                code="voice_personalization.invalid_metadata",
                message="metadata must be an object",
                status_code=422,
            )
        unknown = sorted(set(metadata) - _SAFE_METADATA_KEYS)
        if unknown:
            raise VoiceGovernanceError(
                code="voice_personalization.invalid_metadata",
                message=f"unsupported metadata fields: {', '.join(unknown)}",
                status_code=422,
            )
        result: dict[str, str] = {}
        for key, value in metadata.items():
            normalized = validate_text(value, field=f"metadata.{key}", max_length=160)
            if normalized:
                result[key] = normalized
        return result

    @staticmethod
    def _validate_kind_text(kind: str, source_text: str | None, target_text: str | None) -> None:
        if kind == "vocabulary" and target_text:
            return
        if kind in {"preference", "substitution"} and source_text and target_text:
            return
        if kind == "negative" and source_text:
            return
        raise VoiceGovernanceError(
            code="voice_personalization.invalid_text_pair",
            message="feedback text fields do not match the selected kind",
            status_code=422,
        )

    def _public_feedback(self, feedback: VoiceFeedbackDB) -> dict[str, Any]:
        return {
            "id": feedback.id,
            "profile_id": feedback.profile_id,
            "source_review_id": feedback.source_review_id,
            "kind": feedback.kind,
            "source_text": self._text_codec.decrypt(feedback.source_ciphertext),
            "target_text": self._text_codec.decrypt(feedback.target_ciphertext),
            "metadata": dict(feedback.feedback_metadata or {}),
            "active": bool(feedback.active),
            "consent_id": feedback.consent_id,
            "consent_version": int(feedback.consent_version),
            "created_at": feedback.created_at,
            "updated_at": feedback.updated_at,
        }


voice_personalization_service = VoicePersonalizationService()


def get_voice_personalization_service() -> VoicePersonalizationService:
    return voice_personalization_service
