"""Semantic speech source-correction endpoint with share-session and consent checks."""

from __future__ import annotations

import time
import uuid
from typing import (
    Any,
    Mapping,
)

from flask import (
    current_app,
    request,
)

from agent.auth import check_auth
from agent.common.errors import api_response
from agent.routes.voice_blueprint import voice_bp
from agent.routes.voice_request_policy import (
    _enforce_voice_policy,
)
from agent.routes.voice_request_support import (
    _governance_error,
    _observe,
    _principal,
    _provider_error,
    _read_audio_field,
)
from agent.routes.voice_route_dependencies import voice_route_dependencies
from agent.services.semantic_speech_source_correction_service import (
    SemanticSpeechSourceCorrectionError,
    get_semantic_speech_source_correction_service,
)
from agent.services.voice_governance_domain import (
    VoiceGovernanceError,
    VoicePrincipal,
)
from agent.services.voice_provider import VoiceProviderError
from ananta_contracts.speech_evidence_governance import SpeechEvidenceGovernanceError


@voice_bp.route("/v1/voice/source-corrections", methods=["POST"])
@_observe("source_correction")
@check_auth
def correct_semantic_speech_source():
    """Run one consent-bound source correction through a Hub child task."""

    dependencies = voice_route_dependencies()
    request_started_epoch_ms = time.time_ns() // 1_000_000
    blocked, _policy = _enforce_voice_policy("transcribe")
    if blocked:
        return blocked
    flags = dict(current_app.extensions.get("semantic_media_feature_flags") or {})
    if flags.get("semantic_speech_runtime") is not True:
        return api_response(
            status="error",
            code=403,
            data={"error": {"code": "semantic_speech_disabled", "message": "semantic speech is disabled"}},
        )
    (filename, payload), error = _read_audio_field("file")
    if error:
        return error
    principal = _principal()
    service = get_semantic_speech_source_correction_service()
    try:
        session_id = str(request.form.get("session_id") or "")
        epoch = int(request.form.get("epoch") or 0)
        consent_id = str(request.form.get("consent_id") or "")
        consent_version = int(request.form.get("consent_version") or 0)
        consent_revocation_epoch = int(request.form.get("consent_revocation_epoch") or -1)
        consent_digest = str(request.form.get("consent_digest") or "")

        def authorize_current_state() -> None:
            """Fence delegation and publication against mid-request revocation."""

            session = dependencies.get_share_session_service().get_session(session_id)
            _authorize_semantic_source_session(principal, session, epoch=epoch)
            consent = dependencies.get_speech_evidence_consent_service().get(principal, consent_id)
            _authorize_semantic_source_consent(
                principal,
                consent,
                session_id=session_id,
                epoch=epoch,
                expected_version=consent_version,
                expected_revocation_epoch=consent_revocation_epoch,
                expected_digest=consent_digest,
                now_ms=time.time_ns() // 1_000_000,
            )

        authorize_current_state()
        command = service.command(
            request.form,
            source_audio=payload,
            requested_at_ms=request_started_epoch_ms,
            consent_granted=True,
        )
        profile_id = str(request.form.get("profile_id") or "default")
        audit_id = f"audit-semantic-source-correction-{uuid.uuid4().hex}"
        provider = dependencies.get_voice_provider_service()
        execution = dependencies.run_hub_voice_request(
            operation="semantic_source_correction",
            principal=principal,
            filename=filename,
            payload=payload,
            profile_id=profile_id,
            configuration_session_id=None,
            idempotency_key=str(request.headers.get("Idempotency-Key") or "").strip(),
            idempotency_payload=command.idempotency_payload,
            audit_id=audit_id,
            request_started_epoch_ms=request_started_epoch_ms,
            invoke=lambda remaining, context: provider.transcribe(
                content=payload,
                filename=filename,
                language=str(request.form.get("language") or "").strip() or None,
                recognition_context=context,
                request_id=audit_id,
                deadline_seconds=remaining,
            ),
            transform_result=lambda result, _configuration, _delegation: service.correct(command, result),
            completion_fence=authorize_current_state,
        )
    except SemanticSpeechSourceCorrectionError as exc:
        return api_response(
            status="error",
            code=exc.status_code,
            data={"error": {"code": exc.reason_code, "message": "source correction was rejected"}},
        )
    except SpeechEvidenceGovernanceError as exc:
        return api_response(
            status="error",
            code=exc.status_code,
            data={"error": {"code": exc.reason_code, "message": "speech consent does not authorize correction"}},
        )
    except VoiceProviderError as exc:
        return _provider_error(exc)
    except VoiceGovernanceError as exc:
        return _governance_error(exc)
    except (TypeError, ValueError):
        return api_response(
            status="error",
            code=422,
            data={"error": {"code": "source_correction_context_invalid", "message": "invalid correction context"}},
        )

    dependencies.log_audit(
        "semantic_speech_source_corrected",
        {
            "actor": principal.subject,
            "tenant_id": principal.tenant_id,
            "operation": "semantic_source_correction",
            "policy_decision": "allowed",
            "task_id": execution.task_id,
            "authority": execution.result.get("authority"),
            "reason_code": execution.result.get("reason_code"),
            "correction_attempted": execution.result.get("correction_attempted"),
            "raw_audio_stored": False,
        },
    )
    return api_response(
        data={
            **execution.result,
            "task_id": execution.task_id,
            "idempotent_replay": execution.idempotent_replay,
        }
    )


def _authorize_semantic_source_session(
    principal: VoicePrincipal,
    session: Mapping[str, Any] | None,
    *,
    epoch: int,
) -> None:
    dependencies = voice_route_dependencies()
    now = time.time()
    if not isinstance(session, Mapping) or session.get("revoked_at") is not None:
        raise SemanticSpeechSourceCorrectionError("source_correction_session_inactive", status_code=403)
    expires_at = session.get("expires_at")
    if isinstance(expires_at, (int, float)) and float(expires_at) <= now:
        raise SemanticSpeechSourceCorrectionError("source_correction_session_inactive", status_code=403)
    if int(session.get("security_epoch") or 0) != epoch or str(session.get("security_mode") or "") != "strict_e2ee":
        raise SemanticSpeechSourceCorrectionError("source_correction_epoch_mismatch", status_code=409)
    if str(session.get("tenant_id") or "default") != principal.tenant_id:
        raise SemanticSpeechSourceCorrectionError("source_correction_tenant_mismatch", status_code=403)
    if str(session.get("owner_user_id") or "") == principal.subject:
        return
    participants = dependencies.get_share_session_service().get_participants(str(session.get("id") or ""))
    if not any(
        str(item.get("user_id") or "") == principal.subject and item.get("revoked_at") is None
        for item in participants
    ):
        raise SemanticSpeechSourceCorrectionError("source_correction_membership_required", status_code=403)


def _authorize_semantic_source_consent(
    principal: VoicePrincipal,
    consent,
    *,
    session_id: str,
    epoch: int,
    expected_version: int,
    expected_revocation_epoch: int,
    expected_digest: str,
    now_ms: int,
) -> None:
    if (
        consent.consent_version != expected_version
        or consent.revocation_epoch != expected_revocation_epoch
        or consent.consent_digest != expected_digest
    ):
        raise SpeechEvidenceGovernanceError(
            "speech_consent_stale_claim",
            "source correction consent claim is stale",
            status_code=409,
        )
    common = {
        "tenant_id": principal.tenant_id,
        "owner_subject": consent.owner_subject,
        "speaker_id": consent.speaker_id,
        "recipient_id": consent.recipient_id,
        "direction": consent.direction,
        "pair_id": session_id,
        "session_id": session_id,
        "session_epoch": epoch,
        "purpose": consent.purpose,
        "now_ms": now_ms,
    }
    consent.allows("capture", data_class="audio", **common)
    consent.allows("raw_audio_share", data_class="audio", **common)
    consent.allows("transcript_share", data_class="transcript", **common)
    consent.allows("transcript_share", data_class="correction", **common)
