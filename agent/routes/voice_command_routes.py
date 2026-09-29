"""Voice command and voice goal endpoints."""

from __future__ import annotations

import json
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
from agent.routes.voice_hub_execution import (
    _complete_deferred_hub_voice_execution,
    _fail_deferred_hub_voice_execution,
)
from agent.routes.voice_request_policy import (
    _enforce_voice_policy,
    _voice_privacy_state,
)
from agent.routes.voice_request_support import (
    _governance_error,
    _observe,
    _principal,
    _provider_error,
    _read_audio_field,
)
from agent.routes.voice_route_dependencies import voice_route_dependencies
from agent.services.voice_governance_domain import VoiceGovernanceError
from agent.services.voice_provider import VoiceProviderError


@voice_bp.route("/v1/voice/command", methods=["POST"])
@_observe("command")
@check_auth
def command():
    dependencies = voice_route_dependencies()
    request_started_epoch_ms = time.time_ns() // 1_000_000
    blocked, _policy = _enforce_voice_policy("command")
    if blocked:
        return blocked
    (filename, payload), error = _read_audio_field("file")
    if error:
        return error
    # Primary AudioDecision path; a no-op unless enabled and the request names a decision_profile.
    # Imported here: voice_audio_decision builds on voice_http_adapter, which imports this module.
    from agent.routes.voice_audio_decision import audio_decision_primary_path

    decided, audio_decision_note = audio_decision_primary_path(filename, payload)
    if decided is not None:
        return decided
    audit_id = f"audit-voice-{uuid.uuid4()}"
    principal = _principal()
    profile_id = str(request.form.get("profile_id") or "default")
    configuration_session_id = (
        str(request.form.get("session_id") or request.form.get("configuration_session_id") or "").strip() or None
    )
    idempotency_key = str(request.headers.get("Idempotency-Key") or "").strip()
    context_value = request.form.get("command_context")
    parsed_context: dict[str, Any] | None = None
    if context_value:
        try:
            context_payload = json.loads(context_value)
            parsed_context = dict(context_payload) if isinstance(context_payload, Mapping) else None
        except ValueError:
            parsed_context = None
    try:
        provider = dependencies.get_voice_provider_service()
        execution = dependencies.run_hub_voice_request(
            operation="command",
            principal=principal,
            filename=filename,
            payload=payload,
            profile_id=profile_id,
            configuration_session_id=configuration_session_id,
            idempotency_key=idempotency_key,
            idempotency_payload={"command_context": parsed_context},
            audit_id=audit_id,
            request_started_epoch_ms=request_started_epoch_ms,
            invoke=lambda remaining, _recognition_context: provider.voice_command(
                content=payload,
                filename=filename,
                context=parsed_context,
                request_id=audit_id,
                deadline_seconds=remaining,
            ),
        )
    except VoiceProviderError as exc:
        return _provider_error(exc)
    except VoiceGovernanceError as exc:
        return _governance_error(exc)

    runtime = execution.result
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
        "task_id": execution.task_id,
        "result_ref": execution.result_ref,
        "result_digest": execution.result_digest,
        "idempotent_replay": execution.idempotent_replay,
    }
    if audio_decision_note is not None:
        response["audio_decision"] = audio_decision_note
    dependencies.log_audit(
        "voice_command",
        {
            "actor": principal.subject,
            "tenant_id": principal.tenant_id,
            "operation": "command",
            "policy_decision": "allowed",
            "request_id": audit_id,
            "audit_id": audit_id,
            "endpoint": "/v1/voice/command",
            "provider": runtime.get("provider"),
            "model": runtime.get("model"),
            "audio_size_bytes": len(payload),
            "intent": intent,
            "raw_audio_stored": _voice_privacy_state()["raw_audio_persisted"],
        },
    )
    return api_response(data=response)


@voice_bp.route("/v1/voice/goal", methods=["POST"])
@_observe("goal")
@check_auth
def goal():
    dependencies = voice_route_dependencies()
    request_started_epoch_ms = time.time_ns() // 1_000_000
    blocked, policy = _enforce_voice_policy("goal")
    if blocked:
        return blocked
    if bool(policy.get("require_explicit_approval_for_goal", True)):
        approved = str(request.form.get("approved") or "").strip().lower() in {"1", "true", "yes", "on"}
        if not approved:
            return api_response(
                status="error",
                code=403,
                data={
                    "error": {
                        "code": "policy_denied",
                        "message": "explicit_voice_approval_required",
                        "retriable": False,
                    }
                },
            )
    (filename, payload), error = _read_audio_field("file")
    if error:
        return error
    audit_id = f"audit-voice-{uuid.uuid4()}"
    principal = _principal()
    profile_id = str(request.form.get("profile_id") or "default")
    configuration_session_id = (
        str(request.form.get("session_id") or request.form.get("configuration_session_id") or "").strip() or None
    )
    idempotency_key = str(request.headers.get("Idempotency-Key") or "").strip()
    create_tasks = str(request.form.get("create_tasks") or "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    governance_mode = str(request.form.get("governance_mode") or "").strip()
    try:
        provider = dependencies.get_voice_provider_service()
        execution = dependencies.run_hub_voice_request(
            operation="goal",
            principal=principal,
            filename=filename,
            payload=payload,
            profile_id=profile_id,
            configuration_session_id=configuration_session_id,
            idempotency_key=idempotency_key,
            idempotency_payload={
                "create_tasks": create_tasks,
                "governance_mode": governance_mode,
            },
            audit_id=audit_id,
            request_started_epoch_ms=request_started_epoch_ms,
            invoke=lambda remaining, _recognition_context: provider.voice_command(
                content=payload,
                filename=filename,
                context=None,
                request_id=audit_id,
                deadline_seconds=remaining,
            ),
            defer_completion=True,
        )
    except VoiceProviderError as exc:
        return _provider_error(exc)
    except VoiceGovernanceError as exc:
        return _governance_error(exc)

    runtime = execution.result
    transcript = str(runtime.get("transcript") or runtime.get("text") or "").strip()
    if not transcript:
        _fail_deferred_hub_voice_execution(
            execution,
            ValueError("voice goal transcript is empty"),
        )
        return api_response(
            status="error",
            code=422,
            data={
                "error": {
                    "code": "voice.empty_transcript",
                    "message": "voice transcript is empty",
                    "retriable": False,
                }
            },
        )

    if execution.idempotent_replay and (execution.claim is None or execution.claim.replayed):
        replay_goal_id = str((execution.claim.result_metadata if execution.claim else {}).get("goal_id") or "")
        if not replay_goal_id:
            return api_response(
                status="error",
                code=409,
                data={
                    "error": {
                        "code": "voice.goal_replay_incomplete",
                        "message": "voice goal replay metadata is incomplete",
                        "retriable": True,
                    }
                },
            )
        dependencies.log_audit(
            "voice_goal_replayed",
            {
                "actor": principal.subject,
                "tenant_id": principal.tenant_id,
                "operation": "goal",
                "policy_decision": "allowed",
                "request_id": audit_id,
                "audit_id": audit_id,
                "endpoint": "/v1/voice/goal",
                "goal_id": replay_goal_id,
                "task_id": execution.task_id,
                "result_ref": execution.result_ref,
                "raw_audio_stored": False,
            },
        )
        return api_response(
            data={
                "goal_id": replay_goal_id,
                "transcript": transcript,
                "created_tasks": bool(create_tasks),
                "requires_review": True,
                "audit_id": audit_id,
                "task_id": execution.task_id,
                "result_ref": execution.result_ref,
                "result_digest": execution.result_digest,
                "idempotent_replay": True,
            }
        )

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

    try:
        internal = current_app.test_client().post("/goals", json=goal_payload, headers=headers)
    except Exception as exc:
        _fail_deferred_hub_voice_execution(execution, exc)
        raise
    internal_json = internal.get_json(silent=True) or {}
    if internal.status_code >= 400:
        message = str(internal_json.get("message") or "voice_goal_creation_failed")
        _fail_deferred_hub_voice_execution(
            execution,
            RuntimeError(f"goal policy path rejected request: {message}"),
        )
        dependencies.log_audit(
            "voice_goal_blocked",
            {
                "actor": principal.subject,
                "tenant_id": principal.tenant_id,
                "operation": "goal",
                "policy_decision": "blocked",
                "request_id": audit_id,
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

    goal_data = (internal_json.get("data") or {}).get("goal") or {}
    goal_id = goal_data.get("id")
    _complete_deferred_hub_voice_execution(
        execution,
        {
            "goal_id": goal_id,
            "created_tasks": bool(create_tasks),
        },
    )
    dependencies.log_audit(
        "voice_goal_created",
        {
            "actor": principal.subject,
            "tenant_id": principal.tenant_id,
            "operation": "goal",
            "policy_decision": "allowed",
            "request_id": audit_id,
            "audit_id": audit_id,
            "endpoint": "/v1/voice/goal",
            "goal_id": goal_id,
            "provider": runtime.get("provider"),
            "model": runtime.get("model"),
            "audio_size_bytes": len(payload),
            "raw_audio_stored": _voice_privacy_state()["raw_audio_persisted"],
        },
    )
    return api_response(
        data={
            "goal_id": goal_id,
            "transcript": transcript,
            "created_tasks": bool(create_tasks),
            "requires_review": True,
            "audit_id": audit_id,
            "task_id": execution.task_id,
            "result_ref": execution.result_ref,
            "result_digest": execution.result_digest,
            "idempotent_replay": execution.idempotent_replay,
        }
    )
