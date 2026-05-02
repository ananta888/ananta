from __future__ import annotations

import json
import uuid

from flask import Blueprint, current_app, request

from agent.auth import check_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.services.voice_provider import VoiceProviderError, get_voice_provider_service

voice_bp = Blueprint("voice", __name__)


def _max_audio_mb() -> int:
    app_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    voice_cfg = app_cfg.get("voice_runtime") if isinstance(app_cfg.get("voice_runtime"), dict) else {}
    return int(voice_cfg.get("max_audio_mb") or current_app.config.get("VOICE_MAX_AUDIO_MB") or 25)


def _store_audio_enabled() -> bool:
    app_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    voice_cfg = app_cfg.get("voice_runtime") if isinstance(app_cfg.get("voice_runtime"), dict) else {}
    return bool(voice_cfg.get("store_audio"))


def _read_audio_field(field_name: str = "file") -> tuple[tuple[str, bytes], tuple | None]:
    file = request.files.get(field_name)
    if file is None:
        return ("", b""), api_response(status="error", code=400, data={"error": {"code": "validation.missing_file", "message": "multipart field 'file' is required"}})
    payload = file.read()
    if not payload:
        return ("", b""), api_response(status="error", code=400, data={"error": {"code": "validation.empty_file", "message": "audio payload must not be empty"}})

    max_bytes = _max_audio_mb() * 1024 * 1024
    if len(payload) > max_bytes:
        return ("", b""), api_response(
            status="error",
            code=413,
            data={"error": {"code": "validation.file_too_large", "message": f"audio payload exceeds {_max_audio_mb()}MB limit"}},
        )
    return (file.filename or "audio", payload), None


def _provider_error(exc: VoiceProviderError):
    return api_response(
        status="error",
        code=exc.status_code,
        data={"error": {"code": exc.code, "message": exc.message, "retriable": exc.retriable}},
    )


@voice_bp.route("/v1/voice/capabilities", methods=["GET"])
@check_auth
def capabilities():
    provider = get_voice_provider_service()
    try:
        health = provider.health()
        models = provider.models()
        available = True
    except VoiceProviderError as exc:
        health = {"ok": False, "status": "unavailable", "reason": exc.code}
        models = []
        available = False

    return api_response(
        data={
            "available": available,
            "provider": "voice-runtime",
            "models": models,
            "capabilities": ["audio_input", "transcription", "voice_command", "multimodal_audio_prompt"],
            "limits": {"max_audio_mb": _max_audio_mb()},
            "health": health,
        }
    )


@voice_bp.route("/v1/voice/transcribe", methods=["POST"])
@check_auth
def transcribe():
    (filename, payload), error = _read_audio_field("file")
    if error:
        return error
    provider = get_voice_provider_service()
    audit_id = f"audit-voice-{uuid.uuid4()}"
    try:
        result = provider.transcribe(content=payload, filename=filename, language=request.form.get("language"))
    except VoiceProviderError as exc:
        return _provider_error(exc)

    log_audit(
        "voice_transcribe",
        {
            "audit_id": audit_id,
            "endpoint": "/v1/voice/transcribe",
            "provider": result.get("provider"),
            "model": result.get("model"),
            "duration_ms": result.get("duration_ms"),
            "audio_size_bytes": len(payload),
            "raw_audio_stored": False,
        },
    )
    return api_response(data={**result, "audit_id": audit_id})


@voice_bp.route("/v1/voice/command", methods=["POST"])
@check_auth
def command():
    (filename, payload), error = _read_audio_field("file")
    if error:
        return error
    provider = get_voice_provider_service()
    audit_id = f"audit-voice-{uuid.uuid4()}"
    context_value = request.form.get("command_context")
    parsed_context = None
    if context_value:
        try:
            parsed_context = json.loads(context_value)
        except ValueError:
            parsed_context = None
    try:
        runtime = provider.voice_command(content=payload, filename=filename, context=parsed_context)
    except VoiceProviderError as exc:
        return _provider_error(exc)

    transcript = str(runtime.get("transcript") or runtime.get("text") or "").strip()
    intent = (runtime.get("tool_intent") or {}).get("type")
    confidence = (runtime.get("tool_intent") or {}).get("confidence")
    proposed_goal = transcript[:400] if transcript else None
    response = {
        "transcript": transcript,
        "intent": intent,
        "confidence": confidence,
        "proposed_goal": proposed_goal,
        "requires_approval": True,
        "audit_id": audit_id,
    }
    log_audit(
        "voice_command",
        {
            "audit_id": audit_id,
            "endpoint": "/v1/voice/command",
            "provider": runtime.get("provider"),
            "model": runtime.get("model"),
            "audio_size_bytes": len(payload),
            "intent": intent,
            "raw_audio_stored": False,
        },
    )
    return api_response(data=response)


@voice_bp.route("/v1/voice/goal", methods=["POST"])
@check_auth
def goal():
    (filename, payload), error = _read_audio_field("file")
    if error:
        return error
    provider = get_voice_provider_service()
    audit_id = f"audit-voice-{uuid.uuid4()}"
    try:
        runtime = provider.voice_command(content=payload, filename=filename, context=None)
    except VoiceProviderError as exc:
        return _provider_error(exc)

    transcript = str(runtime.get("transcript") or runtime.get("text") or "").strip()
    if not transcript:
        return api_response(
            status="error",
            code=422,
            data={"error": {"code": "voice.empty_transcript", "message": "voice transcript is empty", "retriable": False}},
        )

    create_tasks = str(request.form.get("create_tasks") or "false").strip().lower() in {"1", "true", "yes", "on"}
    governance_mode = str(request.form.get("governance_mode") or "").strip()

    # Must go through existing goal policy path.
    goal_payload = {
        "goal": transcript,
        "source": "voice",
        "mode": "generic",
        "mode_data": {},
        "create_tasks": bool(create_tasks),
        "execution_preferences": {"voice": {"audit_id": audit_id, "governance_mode": governance_mode}},
    }
    headers = {}
    auth_header = request.headers.get("Authorization")
    if auth_header:
        headers["Authorization"] = auth_header

    internal = current_app.test_client().post("/goals", json=goal_payload, headers=headers)
    internal_json = internal.get_json(silent=True) or {}
    if internal.status_code >= 400:
        message = str(internal_json.get("message") or "voice_goal_creation_failed")
        log_audit(
            "voice_goal_blocked",
            {
                "audit_id": audit_id,
                "endpoint": "/v1/voice/goal",
                "reason": message,
                "status_code": internal.status_code,
                "raw_audio_stored": False,
            },
        )
        return api_response(
            status="error",
            code=internal.status_code,
            data={"error": {"code": "policy_denied", "message": message, "retriable": False}, "audit_id": audit_id},
        )

    goal_data = ((internal_json.get("data") or {}).get("goal") or {})
    goal_id = goal_data.get("id")
    log_audit(
        "voice_goal_created",
        {
            "audit_id": audit_id,
            "endpoint": "/v1/voice/goal",
            "goal_id": goal_id,
            "provider": runtime.get("provider"),
            "model": runtime.get("model"),
            "audio_size_bytes": len(payload),
            "raw_audio_stored": bool(_store_audio_enabled() and False),
        },
    )
    return api_response(
        data={
            "goal_id": goal_id,
            "transcript": transcript,
            "created_tasks": bool(create_tasks),
            "requires_review": True,
            "audit_id": audit_id,
        }
    )
