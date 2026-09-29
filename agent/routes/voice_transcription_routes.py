"""Voice capabilities and transcription endpoints."""

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
from agent.routes.voice_hub_execution import _recover_hub_voice_execution
from agent.routes.voice_recognition_context import _hub_effective_configuration
from agent.routes.voice_request_deadlines import (
    _context_with_remaining_deadline,
    _deadline_epoch_ms,
)
from agent.routes.voice_request_support import (
    _deadline_seconds,
    _enforce_voice_policy,
    _governance_error,
    _max_audio_mb,
    _observe,
    _principal,
    _provider_error,
    _read_audio_field,
    _voice_module,
    _voice_privacy_state,
    _voice_request_ref,
)
from agent.services.voice_admission_service import (
    VoiceAdmissionLease,
    estimate_batch_audio_seconds,
)
from agent.services.voice_delegation_task_service import (
    VoiceDelegationTask,
    get_voice_delegation_task_service,
)
from agent.services.voice_governance_domain import (
    VoiceGovernanceError,
    voice_idempotency_audio_binding,
)
from agent.services.voice_idempotency_service import VoiceIdempotencyService
from agent.services.voice_observability import record_voice_result
from agent.services.voice_provider import VoiceProviderError
from agent.services.voice_transcription_postprocessing_service import get_voice_transcription_postprocessing_service


@voice_bp.route("/v1/voice/capabilities", methods=["GET"])
@_observe("capabilities")
@check_auth
def capabilities():
    blocked, _policy = _enforce_voice_policy("capabilities")
    if blocked:
        return blocked
    provider = _voice_module().get_voice_provider_service()
    try:
        health = provider.health()
        models = provider.models()
        catalog_value = provider.capability_catalog()
        catalog = catalog_value if isinstance(catalog_value, list) else []
        available = True
    except VoiceProviderError as exc:
        health = {"ok": False, "status": "unavailable", "reason": exc.code}
        models = []
        catalog = []
        available = False

    correction_catalog = _voice_module().generative_corrector_capability_bundle(
        current_app.config.get("AGENT_CONFIG", {}) or {}
    )
    correction_models = correction_catalog["correction_models"]
    semantic_speech_enabled = (
        dict(current_app.extensions.get("semantic_media_feature_flags") or {}).get(
            "semantic_speech_runtime"
        )
        is True
    )
    return api_response(
        data={
            "available": available,
            "provider": "voice-runtime",
            "models": models,
            "model_catalog": catalog,
            "correction_models": correction_models,
            "correction_providers": correction_catalog["correction_providers"],
            "correction_default": correction_catalog["correction_default"],
            "capabilities": [
                "audio_input",
                "transcription",
                "voice_command",
                "multimodal_audio_prompt",
                *(
                    ["generative_transcript_correction"]
                    if any(model.get("available") is True for model in correction_models)
                    else []
                ),
                *(["semantic_source_correction"] if available and semantic_speech_enabled else []),
            ],
            "limits": {"max_audio_mb": _max_audio_mb()},
            "privacy": _voice_privacy_state(),
            "health": health,
            "resources": dict(health.get("resources") or {}),
            "routing_details": {
                "owner": "hub",
                "runtime_direct_client_access": False,
                "selection_reason": "hub_voice_policy",
            },
        }
    )


@voice_bp.route("/v1/voice/transcribe", methods=["POST"])
@_observe("transcribe")
@check_auth
def transcribe():
    request_started_epoch_ms = time.time_ns() // 1_000_000
    blocked, _policy = _enforce_voice_policy("transcribe")
    if blocked:
        return blocked
    (filename, payload), error = _read_audio_field("file")
    if error:
        return error
    provider = _voice_module().get_voice_provider_service()
    audit_id = f"audit-voice-{uuid.uuid4()}"
    principal = _principal()
    profile_id = str(request.form.get("profile_id") or "default")
    configuration_session_id = (
        str(request.form.get("session_id") or request.form.get("configuration_session_id") or "").strip() or None
    )
    idempotency_key = str(request.headers.get("Idempotency-Key") or "").strip()
    request_hash = _voice_request_ref(
        principal,
        operation="transcribe",
        idempotency_key=idempotency_key,
    )
    idempotency = VoiceIdempotencyService()
    claim = None
    delegation: VoiceDelegationTask | None = None
    admission_lease: VoiceAdmissionLease | None = None
    admission_service = _voice_module().get_voice_admission_service()
    try:
        context = _voice_module()._recognition_context(
            principal,
            profile_id=profile_id,
            session_id=configuration_session_id,
        )
        deadline = _deadline_seconds()
        effective_configuration = _hub_effective_configuration(context)
        configured_deadline = (
            float(effective_configuration.get("candidate_deadline_sec") or 120.0)
            if isinstance(effective_configuration, dict)
            else 120.0
        )
        deadline_budget = min(deadline if deadline is not None else configured_deadline, configured_deadline)
        absolute_deadline_epoch_ms = _deadline_epoch_ms(
            request_started_epoch_ms=request_started_epoch_ms,
            budget_seconds=deadline_budget,
        )
        if idempotency_key:
            claim = idempotency.begin(
                principal,
                operation="voice.transcribe",
                idempotency_key=idempotency_key,
                payload={
                    "audio_size_bytes": len(payload),
                    "audio_binding": voice_idempotency_audio_binding(
                        principal,
                        operation="voice.transcribe",
                        idempotency_key=idempotency_key,
                        audio=payload,
                    ),
                    "filename": filename,
                    "language": request.form.get("language"),
                    "profile_id": profile_id,
                    "configuration_session_id": configuration_session_id,
                    "effective_configuration": effective_configuration,
                },
            )
            request_hash = _voice_request_ref(
                principal,
                operation="transcribe",
                idempotency_key=idempotency_key,
                claim_id=claim.record_id,
            )
            if claim.replayed:
                result_ref = str(claim.result_metadata.get("result_ref") or "")
                artifact = _voice_module().get_voice_result_artifact_service().get(principal, result_ref)
                return api_response(
                    data={
                        **artifact["result"],
                        "result_ref": result_ref,
                        "task_id": claim.result_metadata.get("task_id"),
                        "idempotent_replay": True,
                        "audit_id": audit_id,
                    }
                )
            recovered = _recover_hub_voice_execution(
                operation="transcribe",
                principal=principal,
                request_ref=request_hash,
                profile_id=profile_id,
                configuration_session_id=configuration_session_id,
                idempotency_key=idempotency_key,
                idempotency=idempotency,
                claim=claim,
                audit_id=audit_id,
                effective_configuration=(
                    effective_configuration if isinstance(effective_configuration, Mapping) else {}
                ),
                deadline_budget=deadline_budget,
                deadline_epoch_ms=absolute_deadline_epoch_ms,
                defer_completion=False,
            )
            if recovered is not None:
                return api_response(
                    data={
                        **recovered.result,
                        "result_ref": recovered.result_ref,
                        "task_id": recovered.task_id,
                        "result_digest": recovered.result_digest,
                        "idempotent_replay": True,
                        "audit_id": audit_id,
                    }
                )
        admission_limits = _voice_module()._voice_admission_limits()
        admission_lease = admission_service.acquire(
            principal,
            audio_seconds=estimate_batch_audio_seconds(
                filename=filename,
                content=payload,
                unknown_audio_seconds=admission_limits.max_audio_seconds_per_request,
            ),
            deadline_epoch_ms=absolute_deadline_epoch_ms,
            limits=admission_limits,
        )
        delegation_service = get_voice_delegation_task_service()
        delegation = delegation_service.start(
            principal,
            request_id=audit_id,
            request_hash=request_hash,
            effective_configuration=effective_configuration if isinstance(effective_configuration, dict) else {},
            deadline_seconds=deadline_budget,
            idempotency_key=idempotency_key or None,
            deadline_epoch_ms=absolute_deadline_epoch_ms,
            profile_id=profile_id,
            configuration_session_id=configuration_session_id,
            parent_task_id=None,
        )
        remaining_deadline = delegation_service.remaining_seconds(delegation)
        if remaining_deadline <= 0:
            raise VoiceProviderError("voice.timeout", "voice request deadline expired", 504, True)
        result: Mapping[str, Any] = provider.transcribe(
            content=payload,
            filename=filename,
            language=request.form.get("language"),
            recognition_context=_context_with_remaining_deadline(context, remaining_deadline),
            request_id=audit_id,
            deadline_seconds=remaining_deadline,
        )
        postprocess = get_voice_transcription_postprocessing_service().apply(
            result,
            effective_configuration if isinstance(effective_configuration, Mapping) else {},
            delegation,
            principal=principal,
            request_id=audit_id,
            language=str(request.form.get("language") or "").strip() or None,
            run_id=str(request.headers.get("X-Run-ID") or "").strip() or None,
            restricted_choice_service=_voice_module().get_voice_restricted_choice_service(),
            generative_judge_service=_voice_module().get_voice_generative_judge_service(),
            generative_corrector_service=_voice_module().get_voice_generative_corrector_service(),
        )
        result = postprocess.result
        choice_applied = postprocess.choice_applied
        choice_reason = postprocess.choice_reason
        choice_manifest_digest = postprocess.choice_manifest_digest
        corrector_applied = postprocess.corrector_applied
        corrector_reason = postprocess.corrector_reason
        artifact = _voice_module().get_voice_result_artifact_service().create(
            principal,
            request_hash=request_hash,
            result=result,
            profile_id=profile_id,
        )
        delegation_service.complete(delegation, result_ref=artifact["id"])
        if claim is not None:
            idempotency.complete(claim, {"result_ref": artifact["id"], "task_id": delegation.task_id})
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
        admission_service.release(admission_lease)

    _voice_module().log_audit(
        "voice_transcribe",
        {
            "actor": principal.subject,
            "tenant_id": principal.tenant_id,
            "operation": "transcribe",
            "policy_decision": "allowed",
            "request_id": audit_id,
            "audit_id": audit_id,
            "endpoint": "/v1/voice/transcribe",
            "provider": result.get("provider"),
            "model": result.get("model"),
            "duration_ms": result.get("duration_ms"),
            "audio_size_bytes": len(payload),
            "pipeline": result.get("pipeline"),
            "backend": result.get("raw_backend"),
            "warnings_count": len(result.get("warnings") or []),
            "restricted_choice_applied": choice_applied,
            "restricted_choice_reason": choice_reason,
            "restricted_choice_manifest_digest": choice_manifest_digest,
            "generative_corrector_applied": corrector_applied,
            "generative_corrector_reason": corrector_reason,
            "raw_audio_stored": _voice_privacy_state()["raw_audio_persisted"],
        },
    )
    record_voice_result(result)
    return api_response(
        data={
            **result,
            "result_ref": artifact["id"],
            "task_id": delegation.task_id,
            "result_digest": artifact["payload_digest"],
            "idempotent_replay": False,
            "audit_id": audit_id,
        }
    )
