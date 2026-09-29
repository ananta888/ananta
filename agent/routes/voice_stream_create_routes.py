"""Voice stream creation endpoint."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any

from flask import request

from agent.auth import check_auth
from agent.common.errors import api_response
from agent.routes.voice_blueprint import voice_bp
from agent.routes.voice_recognition_context import _hub_effective_configuration
from agent.routes.voice_request_deadlines import (
    _assert_stream_preview_context,
    _context_with_remaining_deadline,
    _deadline_epoch_ms,
    _stream_deadline_budget,
    _stream_preview_payload,
    _stream_request_context,
)
from agent.routes.voice_request_support import (
    _enforce_voice_policy,
    _governance_error,
    _max_audio_mb,
    _observe,
    _principal,
    _provider_error,
    _voice_module,
)
from agent.services.voice_admission_service import (
    VoiceAdmissionLease,
    reserve_stream_audio_seconds,
)
from agent.services.voice_delegation_task_service import (
    VoiceDelegationTask,
    get_voice_delegation_task_service,
)
from agent.services.voice_governance_domain import (
    VoiceGovernanceError,
    validate_identifier,
)
from agent.services.voice_idempotency_service import VoiceIdempotencyService
from agent.services.voice_live_run_preview_service import get_voice_live_run_preview_service
from agent.services.voice_observability import record_stream_event
from agent.services.voice_provider import VoiceProviderError
from agent.services.voice_runtime_cleanup_service import (
    VoiceRuntimeCleanupTarget,
    get_voice_runtime_cleanup_service,
)
from agent.services.voice_stream_session_service import get_voice_stream_session_service


@voice_bp.route("/v1/voice/streams", methods=["POST"])
@_observe("stream")
@check_auth
def create_voice_stream():
    request_started_epoch_ms = time.time_ns() // 1_000_000
    blocked, _policy = _enforce_voice_policy("stream")
    if blocked:
        return blocked
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return api_response(
            status="error",
            code=400,
            data={"error": {"code": "validation.invalid_json", "message": "JSON object body is required"}},
        )
    idempotency_key = str(request.headers.get("Idempotency-Key") or "").strip()
    if not idempotency_key:
        return api_response(
            status="error",
            code=400,
            data={"error": {"code": "voice_stream.idempotency_required", "message": "Idempotency-Key is required"}},
        )
    principal = _principal()
    idempotency = VoiceIdempotencyService()
    preview_service = get_voice_live_run_preview_service()
    try:
        preview_binding = preview_service.resolve_optional(
            principal,
            live_run_id=body.get("live_run_id"),
            live_run_segment_sequence=body.get("live_run_segment_sequence"),
        )
    except VoiceGovernanceError as exc:
        return _governance_error(exc)
    try:
        deadline_seconds = max(1.0, min(float(body.get("deadline_seconds") or 120.0), 300.0))
    except (TypeError, ValueError):
        return api_response(
            status="error",
            code=422,
            data={"error": {"code": "voice_stream.invalid_deadline", "message": "deadline_seconds is invalid"}},
        )
    admission_limits = _voice_module()._voice_admission_limits()
    media_type = str(body.get("media_type") or "audio/pcm;rate=16000;channels=1")
    try:
        stream_audio_limit = min(
            admission_limits.max_audio_seconds_per_request,
            (
                float(preview_binding.segment_duration_seconds)
                if preview_binding is not None
                else admission_limits.max_audio_seconds_per_request
            ),
        )
        requested_audio_seconds = float(
            stream_audio_limit
            if body.get("max_audio_seconds") is None
            else body["max_audio_seconds"]
        )
        if requested_audio_seconds <= 0:
            raise ValueError("max_audio_seconds must be positive")
        max_audio_seconds = max(
            0.001,
            min(requested_audio_seconds, stream_audio_limit),
        )
        admission_audio_seconds = reserve_stream_audio_seconds(
            media_type=media_type,
            requested_audio_seconds=max_audio_seconds,
            max_audio_seconds=stream_audio_limit,
        )
    except (TypeError, ValueError):
        return api_response(
            status="error",
            code=422,
            data={"error": {"code": "voice_stream.invalid_audio_budget", "message": "max_audio_seconds is invalid"}},
        )
    profile_id, configuration_session_id, language = _stream_request_context(
        body,
        preview_binding,
    )
    payload: dict[str, Any] = {
        "filename": str(body.get("filename") or "stream.pcm")[:255],
        "language": language,
        "profile_id": profile_id,
        "configuration_session_id": configuration_session_id,
        "media_type": media_type,
        "deadline_seconds": deadline_seconds,
        "max_audio_seconds": max_audio_seconds,
    }
    payload.update(_stream_preview_payload(preview_binding))
    claim = None
    delegation: VoiceDelegationTask | None = None
    admission_lease: VoiceAdmissionLease | None = None
    runtime_session_id = ""
    hub_session_id = f"voice-stream-{uuid.uuid4().hex}"
    session = None
    stream_committed = False
    stream_request_id = ""
    admission_service = _voice_module().get_voice_admission_service()
    try:
        payload["profile_id"] = validate_identifier(payload["profile_id"], field="profile_id")
        _assert_stream_preview_context(
            preview_service,
            principal,
            preview_binding,
            profile_id=payload["profile_id"],
            configuration_session_id=payload["configuration_session_id"],
            language=payload["language"],
        )
        recognition_context = _voice_module()._recognition_context(
            principal,
            profile_id=payload["profile_id"],
            session_id=payload["configuration_session_id"],
        )
        effective_configuration = _hub_effective_configuration(recognition_context)
        configured_deadline = (
            float(effective_configuration.get("candidate_deadline_sec") or 120.0)
            if isinstance(effective_configuration, dict)
            else 120.0
        )
        deadline_budget = _stream_deadline_budget(
            requested_deadline_seconds=payload["deadline_seconds"],
            max_audio_seconds=max_audio_seconds,
            candidate_deadline_seconds=configured_deadline,
        )
        absolute_deadline_epoch_ms = _deadline_epoch_ms(
            request_started_epoch_ms=request_started_epoch_ms,
            budget_seconds=deadline_budget,
        )
        claim = idempotency.begin(
            principal,
            operation="voice_stream.create",
            idempotency_key=idempotency_key,
            payload={**payload, "effective_configuration": effective_configuration},
        )
        if claim.replayed:
            session_id = str(claim.result_metadata.get("session_id") or "")
            session = get_voice_stream_session_service().require(principal, session_id)
            return api_response(data={"stream": session.public(), "idempotent_replay": True})
        if preview_binding is not None:
            preview_service.assert_available(principal, preview_binding)
        admission_lease = admission_service.acquire(
            principal,
            audio_seconds=admission_audio_seconds,
            deadline_epoch_ms=absolute_deadline_epoch_ms,
            limits=admission_limits,
        )
        stream_request_id = f"hub-stream-{uuid.uuid4().hex}"
        delegation_service = get_voice_delegation_task_service()
        delegation = delegation_service.start(
            principal,
            request_id=stream_request_id,
            request_hash=hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            effective_configuration=effective_configuration if isinstance(effective_configuration, dict) else {},
            deadline_seconds=deadline_budget,
            idempotency_key=idempotency_key,
            deadline_epoch_ms=absolute_deadline_epoch_ms,
            profile_id=payload["profile_id"],
            configuration_session_id=payload["configuration_session_id"],
            parent_task_id=(
                preview_binding.parent_task_id
                if preview_binding is not None
                else None
            ),
            operation="live_preview" if preview_binding is not None else "transcribe",
        )
        remaining_deadline = delegation_service.remaining_seconds(delegation)
        if remaining_deadline <= 0:
            raise VoiceProviderError("voice.timeout", "voice stream deadline expired", 504, True)
        runtime_session_id = f"vs_{uuid.uuid4().hex}"
        cleanup = get_voice_runtime_cleanup_service()
        cleanup.stage(
            principal,
            profile_id=payload["profile_id"],
            operation="stream_orphan",
            targets=(
                VoiceRuntimeCleanupTarget(
                    source_session_id=hub_session_id,
                    runtime_session_id=runtime_session_id,
                ),
            ),
            provisional=True,
        )
        runtime = _voice_module().get_voice_provider_service().create_stream(
            filename=payload["filename"],
            language=payload["language"],
            media_type=payload["media_type"],
            deadline_seconds=remaining_deadline,
            max_audio_seconds=max_audio_seconds,
            recognition_context=_context_with_remaining_deadline(recognition_context, remaining_deadline),
            request_id=stream_request_id,
            requested_session_id=runtime_session_id,
        )
        returned_runtime_session_id = str(runtime.get("session_id") or "")
        if not returned_runtime_session_id:
            raise VoiceProviderError(
                "voice.invalid_response",
                "voice runtime returned an invalid stream capability",
                502,
                False,
            )
        if returned_runtime_session_id != runtime_session_id:
            cleanup.stage(
                principal,
                profile_id=payload["profile_id"],
                operation="stream_orphan",
                targets=(
                    VoiceRuntimeCleanupTarget(
                        source_session_id=f"{hub_session_id}-unexpected",
                        runtime_session_id=returned_runtime_session_id,
                    ),
                ),
            )
            raise VoiceProviderError(
                "voice.invalid_response",
                "voice runtime did not honor the Hub-issued stream capability",
                502,
                False,
            )
        try:
            runtime_audio_seconds = float(runtime.get("max_audio_seconds") or max_audio_seconds)
        except (TypeError, ValueError) as exc:
            raise VoiceProviderError(
                "voice.invalid_response",
                "voice runtime returned an invalid stream audio budget",
                502,
                False,
            ) from exc
        if runtime_audio_seconds > max_audio_seconds:
            raise VoiceProviderError(
                "voice.invalid_response",
                "voice runtime expanded the admitted stream audio budget",
                502,
                False,
            )
        if preview_binding is not None:
            preview_service.assert_current(principal, preview_binding)
        session = get_voice_stream_session_service().create(
            principal,
            runtime_session_id=runtime_session_id,
            session_id=hub_session_id,
            deadline_seconds=remaining_deadline,
            profile_id=payload["profile_id"],
            configuration_session_id=payload["configuration_session_id"],
            language=payload["language"],
            effective_configuration=(effective_configuration if isinstance(effective_configuration, dict) else {}),
            task_id=delegation.task_id,
            request_id=stream_request_id,
            admission_lease_id=admission_lease.lease_id,
            max_audio_seconds=max_audio_seconds,
            max_audio_bytes=min(
                _max_audio_mb() * 1024 * 1024,
                (
                    max(1, int(max_audio_seconds * 16_000 * 2))
                    if payload["media_type"] == "audio/pcm;rate=16000;channels=1"
                    else _max_audio_mb() * 1024 * 1024
                ),
            ),
            live_run_id=(
                preview_binding.live_run_id
                if preview_binding is not None
                else None
            ),
            live_run_segment_sequence=(
                preview_binding.live_run_segment_sequence
                if preview_binding is not None
                else None
            ),
        )
        admission_service.release_concurrency(admission_lease)
        admission_lease = None  # The stream session now owns and releases the lease.
        idempotency.complete(claim, {"session_id": session.session_id, "task_id": delegation.task_id})
        stream_committed = True
        _voice_module().log_audit(
            "voice_stream_created",
            {
                "actor": principal.subject,
                "session_id": session.session_id,
                "tenant_id": principal.tenant_id,
                "operation": "live_preview" if preview_binding is not None else "stream",
                "policy_decision": "allowed",
                "request_id": str(runtime.get("request_id") or "hub-stream"),
                "media_type": payload["media_type"],
            },
        )
        record_stream_event("created")
        return api_response(data={"stream": session.public(), "idempotent_replay": False}, code=201)
    except (TypeError, ValueError):
        if delegation is not None:
            get_voice_delegation_task_service().fail(delegation, ValueError("invalid_stream_request"))
        if claim is not None:
            idempotency.abandon(claim)
        return api_response(
            status="error",
            code=422,
            data={"error": {"code": "voice_stream.invalid_deadline", "message": "deadline_seconds is invalid"}},
        )
    except VoiceProviderError as exc:
        if delegation is not None:
            get_voice_delegation_task_service().fail(delegation, exc)
        if claim is not None:
            idempotency.abandon(claim)
        return _provider_error(exc)
    except VoiceGovernanceError as exc:
        if delegation is not None:
            get_voice_delegation_task_service().fail(delegation, exc)
        if claim is not None:
            idempotency.abandon(claim)
        return _governance_error(exc)
    except Exception as exc:
        if delegation is not None:
            get_voice_delegation_task_service().fail(delegation, exc)
        if claim is not None:
            idempotency.abandon(claim)
        raise
    finally:
        if runtime_session_id and not stream_committed:
            cleanup = get_voice_runtime_cleanup_service()
            cleanup.activate_target(
                principal,
                payload["profile_id"],
                hub_session_id,
                operation="stream_orphan",
            )
            if session is not None:
                get_voice_stream_session_service().delete(principal, session.session_id)
            cleanup.retry_profile(principal, payload["profile_id"])
        admission_service.release(admission_lease)
