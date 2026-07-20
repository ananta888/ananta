from __future__ import annotations

from typing import Any, cast

from flask import Blueprint, g, request

from agent.auth import check_user_auth
from agent.common.errors import api_response
from agent.services.speech_evidence_curation_task_service import (
    SpeechCurationTaskError,
    get_speech_evidence_curation_task_service,
)
from agent.services.voice_consent_service import get_voice_consent_service
from agent.services.voice_governance_domain import (
    VoiceGovernanceError,
    VoicePrincipal,
    stable_payload_hash,
    validate_text,
    voice_scope_digest,
)
from agent.services.voice_idempotency_service import VoiceIdempotencyService
from agent.services.voice_personalization_service import get_voice_personalization_service
from agent.services.voice_privacy_service import get_voice_privacy_service
from agent.services.voice_review_service import get_voice_review_service
from agent.services.voice_runtime_cleanup_service import (
    VoiceRuntimeCleanupTarget,
    get_voice_runtime_cleanup_service,
)
from agent.services.voice_stream_session_service import HubVoiceStreamSession, get_voice_stream_session_service
from agent.services.voice_training_export_service import get_voice_training_export_service

voice_governance_bp = Blueprint("voice_governance", __name__)

_FORBIDDEN_AUDIO_FIELDS = {
    "audio",
    "audio_bytes",
    "audio_content",
    "audio_excerpt",
    "raw_audio",
}


def _principal() -> VoicePrincipal:
    identity = dict(getattr(g, "user", {}) or {})
    subject = str(identity.get("sub") or identity.get("username") or "").strip()
    tenant_id = str(identity.get("tenant_id") or identity.get("tenant") or subject).strip()
    if not subject:
        raise VoiceGovernanceError(
            code="voice_governance.subject_required",
            message="authenticated user subject is required",
            status_code=401,
        )
    return VoicePrincipal(tenant_id=tenant_id, subject=subject)


def _json_body() -> dict[str, Any]:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise VoiceGovernanceError(
            code="voice_governance.invalid_json",
            message="JSON object body is required",
            status_code=400,
        )
    forbidden = sorted(set(body) & _FORBIDDEN_AUDIO_FIELDS)
    if forbidden:
        raise VoiceGovernanceError(
            code="voice_governance.raw_audio_not_accepted",
            message="raw audio is not accepted by Hub governance endpoints",
            status_code=422,
        )
    return body


def _idempotency_key() -> str:
    key = str(request.headers.get("Idempotency-Key") or "").strip()
    if not key:
        raise VoiceGovernanceError(
            code="voice_governance.idempotency_key_required",
            message="Idempotency-Key header is required",
            status_code=400,
        )
    return key


def _error(exc: VoiceGovernanceError):
    return api_response(
        status="error",
        code=exc.status_code,
        data={"error": {"code": exc.code, "message": exc.message, "retriable": False}},
    )


def _curation_error(exc: SpeechCurationTaskError):
    return api_response(
        status="error",
        code=exc.status_code,
        data={"error": {"code": exc.reason_code, "message": str(exc), "retriable": False}},
    )


@voice_governance_bp.route("/v1/voice/evidence-curation-tasks", methods=["POST"])
@check_user_auth
def create_speech_evidence_curation_task():
    """Create exactly the Hub-defined curation task for one admission."""

    try:
        body = _json_body()
        if set(body) != {"admission_digest", "confirmed"}:
            raise SpeechCurationTaskError("speech_curation_request_fields_invalid", status_code=422)
        if body.get("confirmed") is not True:
            raise SpeechCurationTaskError("speech_curation_confirmation_required", status_code=403)
        task, created = get_speech_evidence_curation_task_service().create(
            _principal(), admission_digest=str(body.get("admission_digest") or "")
        )
        return api_response(data={"curation_task": task.to_dict()}, code=201 if created else 200)
    except SpeechCurationTaskError as exc:
        return _curation_error(exc)


@voice_governance_bp.route("/v1/voice/consents/<profile_id>", methods=["GET"])
@check_user_auth
def get_consent(profile_id: str):
    try:
        result = get_voice_consent_service().get(_principal(), profile_id)
        return api_response(data={"consent": result})
    except VoiceGovernanceError as exc:
        return _error(exc)


@voice_governance_bp.route("/v1/voice/consents/<profile_id>", methods=["PUT"])
@check_user_auth
def set_consent(profile_id: str):
    try:
        body = _json_body()
        granted = body.get("granted")
        if not isinstance(granted, bool):
            raise VoiceGovernanceError(
                code="voice_consent.invalid_granted",
                message="granted must be a boolean",
                status_code=422,
            )
        categories = body.get("categories")
        if categories is None:
            categories = ["preferences", "text_corrections", "vocabulary"] if granted else []
        principal = _principal()
        result = get_voice_consent_service().set(
            principal,
            profile_id=profile_id,
            granted=granted,
            categories=categories,
            retention_days=body.get("retention_days", 365),
            idempotency_key=_idempotency_key(),
        )
        if not granted:
            runtime_cleanup = get_voice_runtime_cleanup_service()
            recovered_cleanup = runtime_cleanup.retry_pseudonymous_profile(
                principal,
                profile_id,
            )
            if result.get("idempotent_replay") is True:
                result["revoked_stream_count"] = 0
                result.update(recovered_cleanup.public())
                return api_response(data={"consent": result})
            runtime_cleanup.stage_cache_gc(
                principal,
                profile_id=profile_id,
                operation="consent_revoke",
            )

            def stage_runtime_cleanup(sessions: tuple[HubVoiceStreamSession, ...]) -> None:
                runtime_cleanup.stage(
                    principal,
                    profile_id=profile_id,
                    operation="consent_revoke",
                    targets=tuple(
                        VoiceRuntimeCleanupTarget(
                            source_session_id=session.session_id,
                            runtime_session_id=session.runtime_session_id,
                        )
                        for session in sessions
                        if session.runtime_session_id
                    ),
                )

            revoked_sessions = get_voice_stream_session_service().revoke_profile(
                principal,
                profile_id,
                before_remove=stage_runtime_cleanup,
            )
            for session in revoked_sessions:
                if session.task_id:
                    from agent.services.voice_delegation_task_service import (
                        get_voice_delegation_task_service,
                    )

                    get_voice_delegation_task_service().cancel(
                        session.task_id,
                        reason_code="voice_consent_revoked",
                    )
            cleanup_run = runtime_cleanup.retry_profile(principal, profile_id)
            runtime_cleanup.pseudonymize_profile_scope(principal, profile_id)
            result["revoked_stream_count"] = len(revoked_sessions)
            result.update(
                {
                    "runtime_cleanup_pending": (
                        cleanup_run.status.pending_count > 0 or recovered_cleanup.status.pending_count > 0
                    ),
                    "runtime_cleanup_failed_count": (
                        cleanup_run.status.failed_count + recovered_cleanup.status.failed_count
                    ),
                }
            )
        return api_response(data={"consent": result})
    except VoiceGovernanceError as exc:
        return _error(exc)


@voice_governance_bp.route("/v1/voice/reviews", methods=["POST"])
@check_user_auth
def create_review():
    try:
        body = _json_body()
        result = get_voice_review_service().create(
            _principal(),
            profile_id=body.get("profile_id") or "default",
            session_id=body.get("session_id"),
            result_ref=cast(str, body.get("result_ref")),
            candidate_ids=cast(list[str], body.get("candidate_ids")),
            idempotency_key=_idempotency_key(),
        )
        return api_response(data={"review": result}, code=200 if result["idempotent_replay"] else 201)
    except VoiceGovernanceError as exc:
        return _error(exc)


@voice_governance_bp.route("/v1/voice/reviews/<review_id>", methods=["GET"])
@check_user_auth
def get_review(review_id: str):
    try:
        result = get_voice_review_service().get(_principal(), review_id)
        return api_response(data={"review": result})
    except VoiceGovernanceError as exc:
        return _error(exc)


@voice_governance_bp.route("/v1/voice/reviews/<review_id>/decision", methods=["POST"])
@check_user_auth
def decide_review(review_id: str):
    try:
        body = _json_body()
        result = get_voice_review_service().decide(
            _principal(),
            review_id=review_id,
            decision=cast(str, body.get("decision")),
            expected_version=cast(int, body.get("expected_version")),
            selected_candidate_id=body.get("selected_candidate_id"),
            correction_text=body.get("correction_text"),
            idempotency_key=_idempotency_key(),
        )
        return api_response(data={"review": result})
    except VoiceGovernanceError as exc:
        return _error(exc)


@voice_governance_bp.route("/v1/voice/personalization/feedback", methods=["POST"])
@check_user_auth
def add_feedback():
    try:
        body = _json_body()
        result = get_voice_personalization_service().add_feedback(
            _principal(),
            profile_id=body.get("profile_id") or "default",
            review_id=cast(str, body.get("review_id")),
            kind=cast(str, body.get("kind")),
            source_text=body.get("source_text"),
            target_text=body.get("target_text"),
            metadata=body.get("metadata") or {},
            idempotency_key=_idempotency_key(),
        )
        return api_response(data={"feedback": result}, code=200 if result["idempotent_replay"] else 201)
    except VoiceGovernanceError as exc:
        return _error(exc)


@voice_governance_bp.route("/v1/voice/personalization/<profile_id>/export", methods=["GET"])
@check_user_auth
def export_personalization(profile_id: str):
    try:
        result = get_voice_personalization_service().export(_principal(), profile_id)
        return api_response(data={"personalization": result})
    except VoiceGovernanceError as exc:
        return _error(exc)


@voice_governance_bp.route("/v1/voice/personalization/<profile_id>/snapshot", methods=["GET"])
@check_user_auth
def personalization_snapshot(profile_id: str):
    try:
        result = get_voice_personalization_service().snapshot(_principal(), profile_id)
        return api_response(data={"snapshot": result})
    except VoiceGovernanceError as exc:
        return _error(exc)


@voice_governance_bp.route("/v1/voice/personalization/<profile_id>", methods=["DELETE"])
@check_user_auth
def reset_personalization(profile_id: str):
    try:
        _json_body() if request.data else {}
        result = get_voice_personalization_service().reset(
            _principal(),
            profile_id=profile_id,
            idempotency_key=_idempotency_key(),
        )
        return api_response(data={"reset": result})
    except VoiceGovernanceError as exc:
        return _error(exc)


@voice_governance_bp.route("/v1/voice/personalization/<profile_id>/import", methods=["POST"])
@check_user_auth
def import_personalization(profile_id: str):
    try:
        body = _json_body()
        result = get_voice_personalization_service().import_payload(
            _principal(),
            profile_id=profile_id,
            payload=body,
            idempotency_key=_idempotency_key(),
        )
        return api_response(data={"import": result})
    except VoiceGovernanceError as exc:
        return _error(exc)


@voice_governance_bp.route("/v1/voice/privacy/<profile_id>", methods=["DELETE"])
@check_user_auth
def delete_voice_profile(profile_id: str):
    try:
        body = _json_body() if request.data else {}
        if body.get("confirmed") is not True:
            raise VoiceGovernanceError(
                code="voice_privacy.confirmation_required",
                message="explicit confirmation is required",
                status_code=403,
            )
        result = get_voice_privacy_service().delete_profile(
            _principal(),
            profile_id=profile_id,
            idempotency_key=_idempotency_key(),
        )
        return api_response(data={"deletion": result})
    except VoiceGovernanceError as exc:
        return _error(exc)


@voice_governance_bp.route(
    "/v1/voice/personalization/<profile_id>/fine-tuning-export-tasks",
    methods=["POST"],
)
@check_user_auth
def create_fine_tuning_export_task(profile_id: str):
    try:
        body = _json_body()
        if body.get("confirmed") is not True:
            raise VoiceGovernanceError(
                code="voice_training_export.confirmation_required",
                message="explicit confirmation is required",
                status_code=403,
            )
        principal = _principal()
        consent = get_voice_consent_service().require_active(principal, profile_id)
        purpose = validate_text(
            body.get("purpose") or "voice_personalization_fine_tuning_export",
            field="purpose",
            max_length=200,
            required=True,
        )
        license_value = validate_text(
            body.get("license") or "user-provided-private-data",
            field="license",
            max_length=160,
            required=True,
        )
        request_payload = {
            "tenant_id": principal.tenant_id,
            "owner_subject": principal.subject,
            "profile_id": profile_id,
            "purpose": purpose,
            "license": license_value,
            "consent_id": consent.id,
            "consent_version": consent.version,
            "deletion_scope_digest": voice_scope_digest(principal, profile_id),
        }
        idempotency = VoiceIdempotencyService()
        claim = idempotency.begin(
            principal,
            operation=f"voice_training_export.create:{profile_id}",
            idempotency_key=_idempotency_key(),
            payload=request_payload,
        )
        if claim.replayed:
            return api_response(data={**dict(claim.result_metadata), "idempotent_replay": True})
        task_id = f"voice-training-export-{stable_payload_hash(request_payload)[:24]}"
        from agent.services.task_queue_service import get_task_queue_service

        get_task_queue_service().ingest_task(
            task_id=task_id,
            status="todo",
            title="Create approved minimized voice training export",
            description="Create a data export only; do not train or download any model.",
            priority="low",
            created_by=principal.subject,
            source="voice_governance",
            tags=["voice_training_export", "no_training"],
            event_type="voice_training_export_approved",
            event_details={"profile_id": profile_id, "starts_training": False},
            extra_fields={
                "task_kind": "voice_training_export",
                "required_capabilities": ["voice_training_export"],
                "worker_execution_context": {
                    "voice_training_export": {
                        **request_payload,
                        "origin": "explicit_user_approval",
                        "starts_training": False,
                        "deletion_scope": profile_id,
                    }
                },
            },
        )
        try:
            export = get_voice_training_export_service().execute_approved_task(principal, task_id)
        except Exception:
            idempotency.abandon(claim)
            raise
        result_metadata = {
            "task_id": task_id,
            "artifact_ref": export["artifact_ref"],
            "item_count": export["item_count"],
            "starts_training": False,
        }
        idempotency.complete(claim, result_metadata)
        return api_response(data={**result_metadata, "idempotent_replay": False}, code=201)
    except VoiceGovernanceError as exc:
        return _error(exc)
    except Exception:
        return _error(
            VoiceGovernanceError(
                code="voice_training_export.task_creation_failed",
                message="approved export task could not be completed",
                status_code=502,
            )
        )


@voice_governance_bp.route(
    "/v1/voice/personalization/<profile_id>/fine-tuning-exports/<artifact_id>",
    methods=["GET"],
)
@check_user_auth
def get_fine_tuning_export(profile_id: str, artifact_id: str):
    try:
        result = get_voice_training_export_service().get(
            _principal(),
            profile_id=profile_id,
            artifact_id=artifact_id,
        )
        return api_response(data={"training_export": result})
    except VoiceGovernanceError as exc:
        return _error(exc)
