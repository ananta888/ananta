"""Voice command route over the optional AudioDecision specialist.

``POST /v1/voice/audio-decisions/command`` (multipart ``file`` + optional ``profile``, ``field``,
``language``, ``fallback``): audio -> AudioDecision provider -> ``DecisionOutcome`` -> hub gate
with ``VoiceCommandAudioDecisionPolicy`` -> typed hub action (``act``, ``confirm``, ``deny``,
``ask_again``, ``system2``, ``normal_path``). A decision never grants a permission: access is
checked by the voice exposure policy (operation ``command``) before anything else, and only the
hub policy can yield ``act``.

With ``VOICE_AUDIO_DECISION_ENABLED`` off the route answers ``normal_path`` without reading the
audio and without contacting the decision service.
"""
from __future__ import annotations

import uuid

from flask import Blueprint, request
from werkzeug.exceptions import RequestEntityTooLarge

from agent.auth import check_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.routes.voice_http_adapter import (
    deadline_seconds as _deadline_seconds,
)
from agent.routes.voice_http_adapter import (
    enforce_voice_policy as _enforce_voice_policy,
)
from agent.routes.voice_http_adapter import (
    governance_error as _governance_error,
)
from agent.routes.voice_http_adapter import (
    max_audio_mb as _max_audio_mb,
)
from agent.routes.voice_http_adapter import (
    observe as _observe,
)
from agent.routes.voice_http_adapter import (
    principal as _principal,
)
from agent.routes.voice_http_adapter import (
    read_audio_field as _read_audio_field,
)
from agent.services.audio_decision_command_policy import DEFAULT_COMMAND_CATALOG
from agent.services.audio_decision_command_service import (
    NORMAL_PATH_ROUTE,
    AudioDecisionConfigurationError,
    cancellation_token_for,
    get_audio_decision_provider,
    run_audio_decision_command,
)
from agent.services.voice_governance_domain import VoiceGovernanceError

voice_audio_decision_bp = Blueprint("voice_audio_decision", __name__)
_MULTIPART_OVERHEAD_BYTES = 64 * 1024
_DEFAULT_PROFILE = "speech-commands-en"
_ENDPOINT = "/v1/voice/audio-decisions/command"


@voice_audio_decision_bp.before_request
def _bound_audio_decision_request_body() -> None:
    request.max_content_length = _max_audio_mb() * 1024 * 1024 + _MULTIPART_OVERHEAD_BYTES
    request.max_form_memory_size = 64 * 1024
    request.max_form_parts = 12


@voice_audio_decision_bp.errorhandler(RequestEntityTooLarge)
def _audio_decision_request_too_large(_exc: RequestEntityTooLarge):
    return api_response(
        status="error",
        code=413,
        data={
            "error": {
                "code": "validation.file_too_large",
                "message": f"voice request exceeds {_max_audio_mb()}MB audio limit",
            }
        },
    )


def _error(code: int, error_code: str, message: str):
    return api_response(status="error", code=code, data={"error": {"code": error_code, "message": message}})


@voice_audio_decision_bp.route(_ENDPOINT, methods=["POST"])
@_observe("command")
@check_auth
def audio_decision_command():
    blocked, _policy = _enforce_voice_policy("command")
    if blocked:
        return blocked
    try:
        provider = get_audio_decision_provider()
    except AudioDecisionConfigurationError:
        # The message names the variable only; no key material.
        return _error(503, "audio_decision.misconfigured", "audio decision provider is misconfigured")
    if provider is None:
        return api_response(
            data={
                "enabled": False,
                "hub_action": "normal_path",
                "error_code": "audio_decision_disabled",
                "fallback_route": NORMAL_PATH_ROUTE,
                "grants_permission": False,
            }
        )

    profile_id = str(request.form.get("profile") or _DEFAULT_PROFILE).strip()
    profile = DEFAULT_COMMAND_CATALOG.get(profile_id)
    if profile is None or profile_id not in provider.allowed_profiles:
        return _error(422, "audio_decision.profile_not_supported", "profile is not enabled for voice commands")
    field_name = str(request.form.get("field") or profile.default_field).strip()
    if field_name not in profile.fields:
        return _error(422, "audio_decision.field_not_supported", "field is not part of the command profile")
    fallback = str(request.form.get("fallback") or "transcribe").strip().lower()
    if fallback not in {"none", "transcribe"}:
        return _error(422, "audio_decision.invalid_fallback", "fallback must be none or transcribe")
    language = str(request.form.get("language") or profile.language).strip().lower()
    try:
        deadline = _deadline_seconds()
    except VoiceGovernanceError as exc:
        return _governance_error(exc)
    (filename, payload), error = _read_audio_field("file")
    if error:
        return error

    audit_id = f"audit-voice-{uuid.uuid4()}"
    token = cancellation_token_for(deadline if deadline is not None else 30.0)
    result = run_audio_decision_command(
        provider,
        filename=filename,
        content=payload,
        profile=profile_id,
        field_name=field_name,
        language=language,
        fallback=fallback,
        cancellation_token=token,
    )
    principal = _principal()
    log_audit(
        "voice_audio_decision_command",
        {
            "actor": principal.subject,
            "tenant_id": principal.tenant_id,
            "operation": "command",
            "policy_decision": "allowed",
            "request_id": audit_id,
            "audit_id": audit_id,
            "endpoint": _ENDPOINT,
            "audio_size_bytes": len(payload),
            "decision": result.as_audit_dict(),
        },
    )
    return api_response(data={**result.as_response(), "audit_id": audit_id})
