"""AudioDecision voice commands: primary path of ``/v1/voice/command``, facade, and confirmations.

``POST /v1/voice/command`` uses the AudioDecision specialist as its primary path when
``VOICE_AUDIO_DECISION_ENABLED`` is on and the request names a ``decision_profile`` (optional
``decision_field``, ``decision_language``, ``decision_fallback``); see ``audio_decision_primary_path``.
Otherwise the command route behaves exactly as before and nothing here reads configuration or
contacts the decision service. If the provider gives nothing usable (``normal_path``) the command
route continues with its regular voice-runtime pipeline on the same audio.

``POST /v1/voice/audio-decisions/command`` stays as a thin facade over the same helpers for callers
that only want the typed decision (``profile``, ``field``, ``language``, ``fallback``); it answers
``normal_path`` instead of transcribing.

``POST /v1/voice/command/confirm`` (JSON ``confirmation_id``, ``action_type``, ``confirmed``) is the
explicit confirmation request for a ``confirm`` answer: single use, short-lived, bound to principal
and action.

A decision never grants a permission: the voice exposure policy (operation ``command``) runs before
anything else on every route and again right before a handler executes; only the hub policy can
yield ``act``.
"""
from __future__ import annotations

import uuid
from typing import Any

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
from agent.services.audio_decision_command_executor import (
    CONFIRM_ROUTE,
    confirm_voice_command,
    confirmation_ttl_seconds,
    dispatch_audio_decision,
    get_voice_command_confirmation_store,
    get_voice_command_executor,
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
_MAX_CONFIRM_BODY_BYTES = 4 * 1024


@voice_audio_decision_bp.before_request
def _bound_audio_decision_request_body() -> None:
    if request.endpoint == "voice_audio_decision.confirm_audio_decision_command":
        request.max_content_length = _MAX_CONFIRM_BODY_BYTES
        return
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


def _authorized_for_command() -> bool:
    """Exposure policy (operation ``command``) re-checked right before a handler runs."""
    blocked, _policy = _enforce_voice_policy("command")
    return blocked is None


def _provider_or_error():
    """``(provider, None)``; ``(None, None)`` when the feature is off; ``(None, response)`` if misconfigured."""
    try:
        return get_audio_decision_provider(), None
    except AudioDecisionConfigurationError:
        # The message names the variable only; no key material.
        return None, _error(503, "audio_decision.misconfigured", "audio decision provider is misconfigured")


def _decision_parameters(provider, *, prefix: str, default_profile: str):
    """Validate the requested profile/field/fallback before the service is contacted."""
    profile_id = str(request.form.get(f"{prefix}profile") or default_profile).strip()
    profile = DEFAULT_COMMAND_CATALOG.get(profile_id)
    if profile is None or profile_id not in provider.allowed_profiles:
        return None, _error(422, "audio_decision.profile_not_supported", "profile is not enabled for voice commands")
    field_name = str(request.form.get(f"{prefix}field") or profile.default_field).strip()
    if field_name not in profile.fields:
        return None, _error(422, "audio_decision.field_not_supported", "field is not part of the command profile")
    fallback = str(request.form.get(f"{prefix}fallback") or "transcribe").strip().lower()
    if fallback not in {"none", "transcribe"}:
        return None, _error(422, "audio_decision.invalid_fallback", "fallback must be none or transcribe")
    language = str(request.form.get(f"{prefix}language") or profile.language).strip().lower()
    return {"profile": profile_id, "field_name": field_name, "fallback": fallback, "language": language}, None


def _decide_and_dispatch(provider, params: dict[str, str], *, filename: str, payload: bytes, endpoint: str):
    """Run the decision, execute/confirm through the hub executor, audit. Returns the response data."""
    try:
        deadline = _deadline_seconds()
    except VoiceGovernanceError as exc:
        return None, _governance_error(exc)
    principal = _principal()
    audit_id = f"audit-voice-{uuid.uuid4()}"
    token = cancellation_token_for(deadline if deadline is not None else 30.0)
    result = run_audio_decision_command(
        provider,
        filename=filename,
        content=payload,
        cancellation_token=token,
        **params,
    )
    dispatch = dispatch_audio_decision(
        result,
        principal=principal,
        authorize=_authorized_for_command,
        executor=get_voice_command_executor(),
        confirmations=get_voice_command_confirmation_store(),
        ttl_seconds=confirmation_ttl_seconds(),
    )
    log_audit(
        "voice_audio_decision_command",
        {
            "actor": principal.subject,
            "tenant_id": principal.tenant_id,
            "operation": "command",
            "policy_decision": "allowed",
            "request_id": audit_id,
            "audit_id": audit_id,
            "endpoint": endpoint,
            "audio_size_bytes": len(payload),
            "decision": dispatch.audit,
        },
    )
    return {**dispatch.response, "path": "audio_decision", "audit_id": audit_id}, None


def audio_decision_primary_path(filename: str, payload: bytes) -> tuple[Any | None, dict[str, Any] | None]:
    """Primary AudioDecision path of ``/v1/voice/command`` (exposure policy already enforced).

    Returns ``(response, None)`` when the decision path answers, ``(None, note)`` when the command
    route must continue with its regular pipeline (``note`` is ``None`` unless a decision was tried).
    Without ``decision_profile`` in the request or with the feature off nothing is read or contacted.
    """
    if not str(request.form.get("decision_profile") or "").strip():
        return None, None
    provider, error = _provider_or_error()
    if error is not None:
        return error, None
    if provider is None:
        return None, None
    params, error = _decision_parameters(provider, prefix="decision_", default_profile=_DEFAULT_PROFILE)
    if error is not None:
        return error, None
    data, error = _decide_and_dispatch(provider, params, filename=filename, payload=payload, endpoint=NORMAL_PATH_ROUTE)
    if error is not None:
        return error, None
    if data["hub_action"] == "normal_path":
        return None, {
            "hub_action": "normal_path",
            "error_code": data.get("error_code"),
            "audit_id": data["audit_id"],
            "grants_permission": False,
        }
    return api_response(data=data), None


@voice_audio_decision_bp.route(_ENDPOINT, methods=["POST"])
@_observe("command")
@check_auth
def audio_decision_command():
    """Facade: typed decision only; ``normal_path`` is answered, not transcribed."""
    blocked, _policy = _enforce_voice_policy("command")
    if blocked:
        return blocked
    provider, error = _provider_or_error()
    if error is not None:
        return error
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
    params, error = _decision_parameters(provider, prefix="", default_profile=_DEFAULT_PROFILE)
    if error is not None:
        return error
    try:
        _deadline_seconds()
    except VoiceGovernanceError as exc:
        return _governance_error(exc)
    (filename, payload), error = _read_audio_field("file")
    if error:
        return error
    data, error = _decide_and_dispatch(provider, params, filename=filename, payload=payload, endpoint=_ENDPOINT)
    if error is not None:
        return error
    return api_response(data=data)


@voice_audio_decision_bp.route(CONFIRM_ROUTE, methods=["POST"])
@_observe("command")
@check_auth
def confirm_audio_decision_command():
    """Explicit confirmation of a pending ``confirm`` action; the only way such an action runs."""
    blocked, _policy = _enforce_voice_policy("command")
    if blocked:
        return blocked
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _error(400, "validation.invalid_json", "JSON object body is required")
    confirmation_id = body.get("confirmation_id")
    action_type = body.get("action_type")
    confirmed = body.get("confirmed")
    if not isinstance(confirmation_id, str) or not confirmation_id.strip() or len(confirmation_id) > 128:
        return _error(422, "validation.confirmation_id", "confirmation_id is required")
    if not isinstance(action_type, str) or not action_type.strip() or len(action_type) > 128:
        return _error(422, "validation.action_type", "action_type is required")
    if not isinstance(confirmed, bool):
        # Only a literal boolean counts; anything else is neither a confirmation nor a rejection.
        return _error(422, "validation.confirmed", "confirmed must be true or false")

    principal = _principal()
    execution = confirm_voice_command(
        confirmation_id.strip(),
        principal=principal,
        action_type=action_type.strip(),
        confirmed=confirmed,
        authorize=_authorized_for_command,
        executor=get_voice_command_executor(),
        confirmations=get_voice_command_confirmation_store(),
    )
    audit_id = f"audit-voice-{uuid.uuid4()}"
    log_audit(
        "voice_audio_decision_confirmation",
        {
            "actor": principal.subject,
            "tenant_id": principal.tenant_id,
            "operation": "command",
            "policy_decision": "allowed" if execution.executed else "denied",
            "request_id": audit_id,
            "audit_id": audit_id,
            "endpoint": CONFIRM_ROUTE,
            "confirmed": confirmed,
            "execution": execution.as_audit_dict(),
        },
    )
    data = {"execution": execution.as_response(), "audit_id": audit_id, "grants_permission": False}
    if execution.executed or execution.error_code == "confirmation.rejected":
        return api_response(data=data)
    status_code = 410 if execution.error_code == "confirmation.expired" else 403
    return api_response(
        status="error",
        code=status_code,
        data={
            **data,
            "error": {"code": execution.error_code, "message": "voice command confirmation denied", "retriable": False},
        },
    )
