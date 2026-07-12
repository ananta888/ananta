from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from typing import Any, Callable

from agent.common.audit import log_audit
from agent.db_models import VoiceReviewDB
from agent.repositories.voice_governance import VoiceReviewRepository
from agent.repositories.voice_review_decision import VoiceReviewDecisionRepository
from agent.services.voice_governance_domain import (
    VoiceGovernanceError,
    VoicePrincipal,
    validate_identifier,
    validate_text,
)
from agent.services.voice_idempotency_service import VoiceIdempotencyService
from agent.services.voice_result_artifact_service import VoiceResultArtifactService, get_voice_result_artifact_service
from agent.services.voice_sensitive_text_codec import VoiceSensitiveTextCodec, get_voice_sensitive_text_codec

_DECISION_TO_STATE = {
    "accept": "accepted",
    "correct": "corrected",
    "reject": "rejected",
}
_REVIEW_DECISION_LOCKS = tuple(threading.RLock() for _index in range(64))


class VoiceReviewService:
    def __init__(
        self,
        repository: VoiceReviewRepository | None = None,
        decision_repository: VoiceReviewDecisionRepository | None = None,
        idempotency: VoiceIdempotencyService | None = None,
        text_codec: VoiceSensitiveTextCodec | None = None,
        result_artifacts: VoiceResultArtifactService | None = None,
        audit_sink: Callable[[str, dict], None] = log_audit,
    ) -> None:
        self._repository = repository or VoiceReviewRepository()
        self._decision_repository = decision_repository or VoiceReviewDecisionRepository()
        self._idempotency = idempotency or VoiceIdempotencyService()
        self._text_codec = text_codec or get_voice_sensitive_text_codec()
        self._result_artifacts = result_artifacts or get_voice_result_artifact_service()
        self._audit = audit_sink

    def create(
        self,
        principal: VoicePrincipal,
        *,
        profile_id: str,
        session_id: str | None,
        result_ref: str,
        candidate_ids: list[str],
        idempotency_key: str,
    ) -> dict[str, Any]:
        normalized_profile_id = validate_identifier(profile_id, field="profile_id")
        normalized_session_id = (
            validate_identifier(session_id, field="session_id", max_length=160) if session_id else None
        )
        normalized_result_ref = validate_identifier(result_ref, field="result_ref", max_length=200)
        normalized_candidates = self._normalize_candidate_ids(candidate_ids)
        artifact = self._result_artifacts.get(principal, normalized_result_ref)
        if str(artifact.get("profile_id") or "") != normalized_profile_id:
            raise VoiceGovernanceError(
                code="voice_review.result_profile_mismatch",
                message="referenced voice result belongs to another profile",
                status_code=422,
            )
        if set(normalized_candidates) - set(artifact["candidate_ids"]):
            raise VoiceGovernanceError(
                code="voice_review.candidate_artifact_mismatch",
                message="review candidates do not belong to the referenced result artifact",
                status_code=422,
            )
        payload = {
            "profile_id": normalized_profile_id,
            "session_id": normalized_session_id,
            "result_ref": normalized_result_ref,
            "candidate_ids": normalized_candidates,
        }
        claim = self._idempotency.begin(
            principal,
            operation="voice_review.create",
            idempotency_key=idempotency_key,
            payload=payload,
        )
        if claim.replayed:
            review_id = str(claim.result_metadata.get("review_id") or "")
            review = self._require(principal, review_id)
            return {**self._public(review), "idempotent_replay": True}
        if claim.lease_token is None:
            raise RuntimeError("active review idempotency claim has no lease token")
        try:
            review = self._decision_repository.create(
                principal,
                profile_id=normalized_profile_id,
                session_id=normalized_session_id,
                result_ref=normalized_result_ref,
                candidate_ids=normalized_candidates,
                idempotency_record_id=claim.record_id,
                idempotency_lease_token=claim.lease_token,
            )
        except Exception:
            self._idempotency.abandon(claim)
            raise
        self._audit(
            "voice_review_created",
            {
                "tenant_id": principal.tenant_id,
                "owner_subject": principal.subject,
                "profile_id": normalized_profile_id,
                "review_id": review.id,
                "result_ref": normalized_result_ref,
                "candidate_count": len(normalized_candidates),
            },
        )
        return {**self._public(review), "idempotent_replay": False}

    def get(self, principal: VoicePrincipal, review_id: str) -> dict[str, Any]:
        return self._public(self._require(principal, review_id))

    def decide(
        self,
        principal: VoicePrincipal,
        *,
        review_id: str,
        decision: str,
        expected_version: int,
        selected_candidate_id: str | None,
        correction_text: str | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        # The repository's conditional UPDATE remains authoritative across Hub
        # processes. This keyed lock prevents concurrent sessions from sharing
        # the single DB-API connection used by in-memory SQLite deployments.
        lock_index = int(hashlib.sha256(str(review_id).encode("utf-8")).hexdigest()[:8], 16)
        with _REVIEW_DECISION_LOCKS[lock_index % len(_REVIEW_DECISION_LOCKS)]:
            return self._decide(
                principal,
                review_id=review_id,
                decision=decision,
                expected_version=expected_version,
                selected_candidate_id=selected_candidate_id,
                correction_text=correction_text,
                idempotency_key=idempotency_key,
            )

    def _decide(
        self,
        principal: VoicePrincipal,
        *,
        review_id: str,
        decision: str,
        expected_version: int,
        selected_candidate_id: str | None,
        correction_text: str | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        normalized_review_id = validate_identifier(review_id, field="review_id")
        normalized_decision = str(decision or "").strip().lower()
        state = _DECISION_TO_STATE.get(normalized_decision)
        if state is None:
            raise VoiceGovernanceError(
                code="voice_review.invalid_decision",
                message="decision must be accept, correct or reject",
                status_code=422,
            )
        if not isinstance(expected_version, int) or isinstance(expected_version, bool) or expected_version < 1:
            raise VoiceGovernanceError(
                code="voice_review.invalid_version",
                message="expected_version must be a positive integer",
                status_code=422,
            )
        normalized_candidate_id = (
            validate_identifier(selected_candidate_id, field="selected_candidate_id", max_length=160)
            if selected_candidate_id
            else None
        )
        normalized_correction = validate_text(
            correction_text,
            field="correction_text",
            max_length=12000,
            required=normalized_decision == "correct",
        )
        review = self._require(principal, normalized_review_id)
        if normalized_candidate_id and normalized_candidate_id not in set(review.candidate_ids or []):
            raise VoiceGovernanceError(
                code="voice_review.unknown_candidate",
                message="selected_candidate_id does not belong to this review",
                status_code=422,
            )
        if normalized_decision == "accept" and normalized_candidate_id is None:
            raise VoiceGovernanceError(
                code="voice_review.candidate_required",
                message="accept requires selected_candidate_id",
                status_code=422,
            )
        if normalized_decision == "reject":
            normalized_candidate_id = None
            normalized_correction = None
        payload = {
            "review_id": normalized_review_id,
            "decision": normalized_decision,
            "expected_version": expected_version,
            "selected_candidate_id": normalized_candidate_id,
            "correction_text": normalized_correction,
        }
        claim = self._idempotency.begin(
            principal,
            operation=f"voice_review.decide:{normalized_review_id}",
            idempotency_key=idempotency_key,
            payload=payload,
        )
        if claim.replayed:
            return {**self._public(self._require(principal, normalized_review_id)), "idempotent_replay": True}
        decision_payload = {
            "schema_version": "ananta.voice-review-decision.v1",
            "review_id": normalized_review_id,
            "result_ref": review.result_ref,
            "profile_id": review.profile_id,
            "from_version": expected_version,
            "to_version": expected_version + 1,
            "decision": normalized_decision,
            "state": state,
            "candidate_ids": list(review.candidate_ids or []),
            "selected_candidate_id": normalized_candidate_id,
            "correction_text": normalized_correction,
            "immutable": True,
        }
        canonical = json.dumps(
            decision_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        if claim.lease_token is None:
            raise RuntimeError("active review-decision idempotency claim has no lease token")
        try:
            review, decision_artifact = self._decision_repository.decide(
                principal,
                review_id=normalized_review_id,
                expected_version=expected_version,
                state=state,
                selected_candidate_id=normalized_candidate_id,
                correction_ciphertext=self._text_codec.encrypt(normalized_correction),
                artifact={
                    "id": f"voice-result-{uuid.uuid4()}",
                    "request_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                    "payload_ciphertext": self._text_codec.encrypt(canonical),
                    "payload_digest": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                    "candidate_ids": list(review.candidate_ids or []),
                    "expires_at": time.time()
                    + self._result_artifacts.retention_seconds_for(principal, review.profile_id),
                },
                idempotency_record_id=claim.record_id,
                idempotency_lease_token=claim.lease_token,
            )
        except Exception:
            self._idempotency.abandon(claim)
            raise
        self._audit(
            "voice_review_decided",
            {
                "tenant_id": principal.tenant_id,
                "owner_subject": principal.subject,
                "profile_id": review.profile_id,
                "review_id": review.id,
                "decision": normalized_decision,
                "selected_candidate_id": normalized_candidate_id,
                "has_manual_correction": bool(normalized_correction),
                "review_version": review.version,
                "decision_artifact_ref": review.decision_artifact_id,
            },
        )
        return {**self._public(review), "idempotent_replay": False}

    def require_for_feedback(
        self,
        principal: VoicePrincipal,
        review_id: str,
        *,
        profile_id: str,
    ) -> VoiceReviewDB:
        review = self._require(principal, review_id)
        if review.profile_id != profile_id:
            raise VoiceGovernanceError(
                code="voice_review.profile_mismatch",
                message="voice review belongs to another profile",
                status_code=409,
            )
        if review.state not in {"accepted", "corrected"}:
            raise VoiceGovernanceError(
                code="voice_review.decision_required",
                message="feedback requires an accepted or corrected review",
                status_code=409,
            )
        return review

    def _require(self, principal: VoicePrincipal, review_id: str) -> VoiceReviewDB:
        normalized_review_id = validate_identifier(review_id, field="review_id")
        review = self._repository.get(principal, normalized_review_id)
        if review is None:
            raise VoiceGovernanceError(
                code="voice_review.not_found",
                message="voice review not found",
                status_code=404,
            )
        return review

    @staticmethod
    def _normalize_candidate_ids(candidate_ids: list[str]) -> list[str]:
        if not isinstance(candidate_ids, list) or not 1 <= len(candidate_ids) <= 32:
            raise VoiceGovernanceError(
                code="voice_review.invalid_candidates",
                message="candidate_ids must contain between 1 and 32 items",
                status_code=422,
            )
        normalized = [validate_identifier(item, field="candidate_id", max_length=160) for item in candidate_ids]
        if len(set(normalized)) != len(normalized):
            raise VoiceGovernanceError(
                code="voice_review.duplicate_candidates",
                message="candidate_ids must be unique",
                status_code=422,
            )
        return normalized

    def _public(self, review: VoiceReviewDB) -> dict[str, Any]:
        return {
            "id": review.id,
            "profile_id": review.profile_id,
            "session_id": review.session_id,
            "result_ref": review.result_ref,
            "candidate_ids": list(review.candidate_ids or []),
            "state": review.state,
            "selected_candidate_id": review.selected_candidate_id,
            "correction_text": self._text_codec.decrypt(review.correction_ciphertext),
            "decision_artifact_ref": review.decision_artifact_id,
            "version": int(review.version),
            "created_at": review.created_at,
            "updated_at": review.updated_at,
        }


voice_review_service = VoiceReviewService()


def get_voice_review_service() -> VoiceReviewService:
    return voice_review_service
