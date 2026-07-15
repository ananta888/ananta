from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable, Mapping

from flask import Blueprint, current_app, g, request
from werkzeug.exceptions import RequestEntityTooLarge

from agent.auth import check_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.services.exposure_policy_service import get_exposure_policy_service
from agent.services.voice_admission_service import (
    VoiceAdmissionLease,
    VoiceAdmissionLimits,
    estimate_batch_audio_seconds,
    get_voice_admission_service,
    reserve_stream_audio_seconds,
)
from agent.services.voice_configuration_service import get_voice_configuration_service
from agent.services.voice_delegation_task_service import (
    VoiceDelegationTask,
    get_voice_delegation_task_service,
)
from agent.services.voice_generative_corrector_service import (
    generative_corrector_capabilities,
    generative_corrector_capability_bundle,
    get_voice_generative_corrector_service,
    resolve_auto_corrector_configuration,
    resolve_inherited_corrector_configuration,
)
from agent.services.voice_generative_judge_service import get_voice_generative_judge_service
from agent.services.voice_governance_domain import (
    VoiceGovernanceError,
    VoicePrincipal,
    validate_identifier,
    voice_idempotency_audio_binding,
)
from agent.services.voice_idempotency_service import VoiceIdempotencyClaim, VoiceIdempotencyService
from agent.services.voice_observability import record_stream_event, record_voice_request, record_voice_result
from agent.services.voice_personalization_service import get_voice_personalization_service
from agent.services.voice_provider import VoiceProviderError, get_voice_provider_service
from agent.services.voice_restricted_choice_service import (
    get_voice_restricted_choice_service,
    new_voice_choice_run_id,
    voice_choice_policy_hash,
)
from agent.services.voice_result_artifact_service import get_voice_result_artifact_service
from agent.services.voice_runtime_cleanup_service import (
    VoiceRuntimeCleanupTarget,
    get_voice_runtime_cleanup_service,
)
from agent.services.voice_stream_session_service import get_voice_stream_session_service

voice_bp = Blueprint("voice", __name__)
_VOICE_MULTIPART_OVERHEAD_BYTES = 256 * 1024
_VOICE_MAX_FORM_MEMORY_BYTES = 256 * 1024
_VOICE_MAX_FORM_PARTS = 32


@voice_bp.before_request
def _bound_voice_request_body_before_form_parsing() -> None:
    """Apply parser-level limits before Werkzeug materializes multipart data."""

    if request.mimetype == "multipart/form-data":
        request.max_content_length = _max_audio_mb() * 1024 * 1024 + _VOICE_MULTIPART_OVERHEAD_BYTES
        request.max_form_memory_size = _VOICE_MAX_FORM_MEMORY_BYTES
        request.max_form_parts = _VOICE_MAX_FORM_PARTS
    elif request.endpoint == "voice.push_voice_stream_chunk":
        request.max_content_length = 1024 * 1024


@voice_bp.errorhandler(RequestEntityTooLarge)
def _voice_request_too_large(_exc: RequestEntityTooLarge):
    if request.endpoint == "voice.push_voice_stream_chunk":
        return api_response(
            status="error",
            code=413,
            data={
                "error": {
                    "code": "voice_stream.invalid_chunk",
                    "message": "chunk must contain at most 1MB",
                }
            },
        )
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


@dataclass(frozen=True)
class _HubVoiceExecution:
    result: dict[str, Any]
    result_ref: str
    result_digest: str
    task_id: str
    idempotent_replay: bool
    idempotency: VoiceIdempotencyService
    claim: VoiceIdempotencyClaim | None
    delegation: VoiceDelegationTask | None


def _observe(operation: str):
    """Measure a Hub endpoint without using tenant- or content-derived labels."""

    def decorator(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            started = time.monotonic()
            try:
                response = function(*args, **kwargs)
            except Exception:
                record_voice_request(
                    operation=operation,
                    outcome="failed",
                    error_code="model_error",
                    duration_seconds=time.monotonic() - started,
                )
                raise
            status_code, error_code = _response_observation(response)
            outcome = "succeeded" if status_code < 400 else "blocked" if status_code in {401, 403} else "failed"
            record_voice_request(
                operation=operation,
                outcome=outcome,
                error_code=error_code,
                duration_seconds=time.monotonic() - started,
            )
            return response

        return wrapped

    return decorator


def _response_observation(response) -> tuple[int, str]:
    response_value = response[0] if isinstance(response, tuple) and response else response
    status_value = response[1] if isinstance(response, tuple) and len(response) > 1 else None
    status_code = (
        int(status_value) if isinstance(status_value, int) else int(getattr(response_value, "status_code", 200))
    )
    payload = response_value.get_json(silent=True) if hasattr(response_value, "get_json") else None
    envelope = payload if isinstance(payload, dict) else {}
    data_value = envelope.get("data")
    data: dict[str, Any] = data_value if isinstance(data_value, dict) else {}
    error_value = data.get("error")
    error: dict[str, Any] = error_value if isinstance(error_value, dict) else {}
    return status_code, str(error.get("code") or ("ok" if status_code < 400 else "other"))


def _max_audio_mb() -> int:
    app_cfg = _mapping(current_app.config.get("AGENT_CONFIG"))
    voice_cfg = _mapping(app_cfg.get("voice_runtime"))
    return int(voice_cfg.get("max_audio_mb") or current_app.config.get("VOICE_MAX_AUDIO_MB") or 25)


def _voice_admission_limits() -> VoiceAdmissionLimits:
    app_cfg = _mapping(current_app.config.get("AGENT_CONFIG"))
    voice_cfg = _mapping(app_cfg.get("voice_runtime"))
    return VoiceAdmissionLimits(
        max_concurrent_requests=int(
            voice_cfg.get("hub_max_concurrent_requests")
            or current_app.config.get("VOICE_HUB_MAX_CONCURRENT_REQUESTS")
            or os.environ.get("VOICE_HUB_MAX_CONCURRENT_REQUESTS", "2")
        ),
        max_queue_depth=int(
            voice_cfg.get("max_queue_depth")
            or current_app.config.get("VOICE_MAX_QUEUE_DEPTH")
            or os.environ.get("VOICE_MAX_QUEUE_DEPTH", "16")
        ),
        max_inflight_audio_seconds=float(
            voice_cfg.get("hub_max_inflight_audio_seconds")
            or current_app.config.get("VOICE_HUB_MAX_INFLIGHT_AUDIO_SECONDS")
            or os.environ.get("VOICE_HUB_MAX_INFLIGHT_AUDIO_SECONDS", "7200")
        ),
        max_audio_seconds_per_request=float(
            voice_cfg.get("max_audio_duration_sec")
            or current_app.config.get("VOICE_MAX_AUDIO_DURATION_SEC")
            or os.environ.get("VOICE_MAX_AUDIO_DURATION_SEC", "3600")
        ),
    )


def _deadline_epoch_ms(*, request_started_epoch_ms: int, budget_seconds: float) -> int:
    return request_started_epoch_ms + max(1, round(float(budget_seconds) * 1000))


def _context_with_remaining_deadline(context: dict | None, remaining_seconds: float) -> dict | None:
    if not isinstance(context, dict):
        return context
    projected = deepcopy(context)
    # Hub-only policy is retained during orchestration but never crosses the
    # runtime boundary. The Runtime receives only its strict execution subset.
    projected.pop("_hub_configuration", None)
    configuration = projected.get("configuration")
    if isinstance(configuration, dict):
        configured = float(configuration.get("candidate_deadline_sec") or remaining_seconds)
        configuration["candidate_deadline_sec"] = max(0.001, min(configured, remaining_seconds))
    return projected


def _store_audio_enabled() -> bool:
    app_cfg = _mapping(current_app.config.get("AGENT_CONFIG"))
    voice_cfg = _mapping(app_cfg.get("voice_runtime"))
    return bool(voice_cfg.get("store_audio"))


def _voice_privacy_state() -> dict:
    # Raw audio persistence is intentionally fail-closed until explicit storage wiring exists.
    return {
        "store_audio_requested": bool(_store_audio_enabled()),
        "store_audio_effective": False,
        "effective_audio_retention": "none",
        "policy_hint": "raw_audio_persistence_not_wired",
        "raw_audio_persisted": False,
    }


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _voice_request_ref(
    principal: VoicePrincipal,
    *,
    operation: str,
    idempotency_key: str,
    claim_id: str = "",
) -> str:
    """Return an opaque correlation reference that never fingerprints audio.

    Idempotent requests receive a stable, scope-bound reference so a durable
    artifact can be recovered after a Hub crash. Requests without an
    idempotency key receive an unlinkable random reference.
    """

    if not idempotency_key:
        return f"voice-request-{uuid.uuid4().hex}"
    canonical_scope = json.dumps(
        {
            "tenant_id": principal.tenant_id,
            "owner_subject": principal.subject,
            "operation": str(operation),
            "idempotency_key": str(idempotency_key),
            "claim_id": str(claim_id),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return f"voice-request-{hashlib.sha256(canonical_scope).hexdigest()}"


def _read_audio_field(field_name: str = "file") -> tuple[tuple[str, bytes], Any | None]:
    file = request.files.get(field_name)
    if file is None:
        return ("", b""), api_response(
            status="error",
            code=400,
            data={"error": {"code": "validation.missing_file", "message": "multipart field 'file' is required"}},
        )
    max_bytes = _max_audio_mb() * 1024 * 1024
    payload = file.stream.read(max_bytes + 1)
    if not payload:
        return ("", b""), api_response(
            status="error",
            code=400,
            data={"error": {"code": "validation.empty_file", "message": "audio payload must not be empty"}},
        )

    if len(payload) > max_bytes:
        return ("", b""), api_response(
            status="error",
            code=413,
            data={
                "error": {
                    "code": "validation.file_too_large",
                    "message": f"audio payload exceeds {_max_audio_mb()}MB limit",
                }
            },
        )
    return (file.filename or "audio", payload), None


def _provider_error(exc: VoiceProviderError):
    return api_response(
        status="error",
        code=exc.status_code,
        data={"error": {"code": exc.code, "message": exc.message, "retriable": exc.retriable}},
    )


def _principal() -> VoicePrincipal:
    identity = dict(getattr(g, "user", {}) or getattr(g, "auth_payload", {}) or {})
    subject = str(identity.get("sub") or identity.get("username") or identity.get("agent_id") or "").strip()
    tenant_id = str(identity.get("tenant_id") or identity.get("tenant") or subject).strip()
    return VoicePrincipal(tenant_id=tenant_id, subject=subject)


def _audit_identity() -> tuple[str, str]:
    identity = dict(getattr(g, "user", {}) or getattr(g, "auth_payload", {}) or {})
    actor = str(identity.get("sub") or identity.get("username") or identity.get("agent_id") or "authenticated")
    tenant_id = str(identity.get("tenant_id") or identity.get("tenant") or actor)
    return actor[:128], tenant_id[:128]


def _governance_error(exc: VoiceGovernanceError):
    return api_response(
        status="error",
        code=exc.status_code,
        data={"error": {"code": exc.code, "message": exc.message, "retriable": False}},
    )


def _deadline_seconds() -> float | None:
    raw = request.headers.get("X-Ananta-Deadline-Seconds") or request.form.get("deadline_seconds")
    if raw is None:
        return None
    try:
        return max(0.1, min(float(raw), 300.0))
    except (TypeError, ValueError) as exc:
        raise VoiceGovernanceError(
            code="voice.invalid_deadline",
            message="deadline_seconds must be numeric",
            status_code=422,
        ) from exc


def _recognition_context(
    principal: VoicePrincipal,
    *,
    profile_id: str | None = None,
    session_id: str | None = None,
) -> dict | None:
    profile_id = profile_id or (str(request.form.get("profile_id") or "").strip() or None)
    session_id = session_id or (str(request.form.get("session_id") or "").strip() or None)
    configuration = get_voice_configuration_service().resolve(
        principal,
        legacy_global=current_app.config.get("AGENT_CONFIG", {}) or {},
        profile_id=profile_id,
        session_id=session_id,
    )
    hub_configuration = resolve_inherited_corrector_configuration(
        configuration.effective,
        current_app.config.get("AGENT_CONFIG", {}) or {},
    )
    if str(hub_configuration.get("generative_corrector_model") or "").casefold() == "auto":
        hub_configuration = resolve_auto_corrector_configuration(
            hub_configuration,
            generative_corrector_capabilities(),
        )
    context: dict = {
        "schema_version": "ananta.voice-recognition-context.v1",
        "configuration": _runtime_voice_configuration(configuration.effective),
        "_hub_configuration": deepcopy(hub_configuration),
    }
    if profile_id and configuration.effective["feature_flags"].get("personalization"):
        snapshot = get_voice_personalization_service().snapshot(principal, profile_id)
        context["personalization"] = {
            "schema_version": snapshot["schema_version"],
            "version": snapshot["version"],
            "consent_id": snapshot["consent_id"],
            "consent_version": snapshot["consent_version"],
            "consent_granted": snapshot["consent_granted"],
            "revocation_epoch": snapshot["revocation_epoch"],
            "expires_at": snapshot["expires_at"],
            "vocabulary": list(snapshot["vocabulary"]),
            "substitutions": list(snapshot["substitutions"]),
            "preferences": list(snapshot["preferences"]),
            "weights": dict(snapshot["weights"]),
            "persistence_owner": "hub",
            "runtime_persistence_allowed": False,
        }
    return context


def _runtime_voice_configuration(effective: Mapping[str, Any]) -> dict[str, Any]:
    """Project Hub-owned correction policy onto the strict Voice Runtime port."""

    runtime_fields = {
        "transport_mode",
        "recognition_strategy",
        "routing_strategy",
        "correction_policy",
        "review_policy",
        "primary_backend",
        "secondary_backends",
        "max_parallel_backends",
        "candidate_deadline_sec",
        "confidence_threshold",
        "enhancement_variants",
        "diarization_backend",
        "feature_flags",
    }
    projected = {
        key: deepcopy(value)
        for key, value in effective.items()
        if key in runtime_fields
    }
    if projected.get("correction_policy") == "generative_rewrite":
        projected["correction_policy"] = "deterministic"
    flags = projected.get("feature_flags")
    if isinstance(flags, dict):
        flags.pop("generative_corrector", None)
    return projected


def _hub_effective_configuration(context: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(context, Mapping):
        return {}
    value = context.get("_hub_configuration", context.get("configuration"))
    return dict(value) if isinstance(value, Mapping) else {}


def _recover_hub_voice_execution(
    *,
    operation: str,
    principal: VoicePrincipal,
    request_ref: str,
    profile_id: str,
    configuration_session_id: str | None,
    idempotency_key: str,
    idempotency: VoiceIdempotencyService,
    claim: VoiceIdempotencyClaim,
    audit_id: str,
    effective_configuration: Mapping[str, Any],
    deadline_budget: float,
    deadline_epoch_ms: int,
    defer_completion: bool,
) -> _HubVoiceExecution | None:
    """Recover an artifact-first Voice request without invoking its provider."""

    if claim.replayed:
        return None
    artifact = get_voice_result_artifact_service().find_live_envelope(
        principal,
        request_ref=request_ref,
        profile_id=profile_id,
    )
    if artifact is None:
        return None
    delegation_service = get_voice_delegation_task_service()
    delegation = delegation_service.start(
        principal,
        request_id=audit_id,
        request_hash=request_ref,
        effective_configuration=dict(effective_configuration),
        deadline_seconds=deadline_budget,
        idempotency_key=idempotency_key,
        deadline_epoch_ms=deadline_epoch_ms,
        profile_id=profile_id,
        configuration_session_id=configuration_session_id,
        parent_task_id=None,
        operation=operation,
    )
    if not defer_completion:
        delegation_service.complete(delegation, result_ref=str(artifact["id"]))
        idempotency.complete(
            claim,
            {"result_ref": artifact["id"], "task_id": delegation.task_id},
        )
    return _HubVoiceExecution(
        result=dict(artifact["result"]),
        result_ref=str(artifact["id"]),
        result_digest=str(artifact["payload_digest"]),
        task_id=delegation.task_id,
        idempotent_replay=True,
        idempotency=idempotency,
        claim=claim,
        delegation=delegation,
    )


def _execute_hub_voice_request(
    *,
    operation: str,
    principal: VoicePrincipal,
    filename: str,
    payload: bytes,
    profile_id: str,
    configuration_session_id: str | None,
    idempotency_key: str,
    idempotency_payload: dict[str, Any],
    audit_id: str,
    request_started_epoch_ms: int,
    invoke: Callable[[float, dict | None], Mapping[str, Any]],
    defer_completion: bool = False,
) -> _HubVoiceExecution:
    """Run one bounded provider call through Hub admission and task ownership."""

    idempotency = VoiceIdempotencyService()
    claim: VoiceIdempotencyClaim | None = None
    delegation: VoiceDelegationTask | None = None
    admission_lease: VoiceAdmissionLease | None = None
    admission_service = get_voice_admission_service()
    request_hash = _voice_request_ref(
        principal,
        operation=operation,
        idempotency_key=idempotency_key,
    )
    try:
        recognition_context = _recognition_context(
            principal,
            profile_id=profile_id,
            session_id=configuration_session_id,
        )
        effective_configuration = _hub_effective_configuration(recognition_context)
        configured_deadline = (
            float(effective_configuration.get("candidate_deadline_sec") or 120.0)
            if isinstance(effective_configuration, dict)
            else 120.0
        )
        requested_deadline = _deadline_seconds()
        deadline_budget = min(
            requested_deadline if requested_deadline is not None else configured_deadline,
            configured_deadline,
        )
        absolute_deadline_epoch_ms = _deadline_epoch_ms(
            request_started_epoch_ms=request_started_epoch_ms,
            budget_seconds=deadline_budget,
        )
        if idempotency_key:
            claim = idempotency.begin(
                principal,
                operation=f"voice.{operation}",
                idempotency_key=idempotency_key,
                payload={
                    "operation": operation,
                    "audio_size_bytes": len(payload),
                    "audio_binding": voice_idempotency_audio_binding(
                        principal,
                        operation=f"voice.{operation}",
                        idempotency_key=idempotency_key,
                        audio=payload,
                    ),
                    "filename": filename,
                    "profile_id": profile_id,
                    "configuration_session_id": configuration_session_id,
                    "effective_configuration": effective_configuration,
                    **idempotency_payload,
                },
            )
            request_hash = _voice_request_ref(
                principal,
                operation=operation,
                idempotency_key=idempotency_key,
                claim_id=claim.record_id,
            )
            if claim.replayed:
                result_ref = str(claim.result_metadata.get("result_ref") or "")
                artifact = get_voice_result_artifact_service().get(principal, result_ref)
                return _HubVoiceExecution(
                    result=dict(artifact["result"]),
                    result_ref=result_ref,
                    result_digest=str(artifact["payload_digest"]),
                    task_id=str(claim.result_metadata.get("task_id") or ""),
                    idempotent_replay=True,
                    idempotency=idempotency,
                    claim=claim,
                    delegation=None,
                )
            recovered = _recover_hub_voice_execution(
                operation=operation,
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
                defer_completion=defer_completion,
            )
            if recovered is not None:
                return recovered

        admission_limits = _voice_admission_limits()
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
            effective_configuration=(effective_configuration if isinstance(effective_configuration, dict) else {}),
            deadline_seconds=deadline_budget,
            idempotency_key=idempotency_key or None,
            deadline_epoch_ms=absolute_deadline_epoch_ms,
            profile_id=profile_id,
            configuration_session_id=configuration_session_id,
            parent_task_id=None,
            operation=operation,
        )
        remaining_deadline = delegation_service.remaining_seconds(delegation)
        if remaining_deadline <= 0:
            raise VoiceProviderError("voice.timeout", "voice request deadline expired", 504, True)
        result = dict(
            invoke(
                remaining_deadline,
                _context_with_remaining_deadline(recognition_context, remaining_deadline),
            )
        )
        artifact = get_voice_result_artifact_service().create(
            principal,
            request_hash=request_hash,
            result=result,
            profile_id=profile_id,
        )
        if not defer_completion:
            delegation_service.complete(delegation, result_ref=artifact["id"])
            if claim is not None:
                idempotency.complete(
                    claim,
                    {"result_ref": artifact["id"], "task_id": delegation.task_id},
                )
        return _HubVoiceExecution(
            result=result,
            result_ref=str(artifact["id"]),
            result_digest=str(artifact["payload_digest"]),
            task_id=delegation.task_id,
            idempotent_replay=False,
            idempotency=idempotency,
            claim=claim,
            delegation=delegation,
        )
    except Exception as exc:
        if delegation is not None:
            get_voice_delegation_task_service().fail(delegation, exc)
        if claim is not None:
            idempotency.abandon(claim)
        raise
    finally:
        admission_service.release(admission_lease)


def _complete_deferred_hub_voice_execution(
    execution: _HubVoiceExecution,
    metadata: Mapping[str, Any] | None = None,
) -> None:
    if execution.idempotent_replay and (
        execution.claim is None or execution.claim.replayed
    ):
        return
    if execution.delegation is not None:
        get_voice_delegation_task_service().complete(
            execution.delegation,
            result_ref=execution.result_ref,
        )
    if execution.claim is not None:
        execution.idempotency.complete(
            execution.claim,
            {
                "result_ref": execution.result_ref,
                "task_id": execution.task_id,
                **dict(metadata or {}),
            },
        )


def _fail_deferred_hub_voice_execution(
    execution: _HubVoiceExecution,
    exc: BaseException,
) -> None:
    if execution.idempotent_replay and (
        execution.claim is None or execution.claim.replayed
    ):
        return
    if execution.delegation is not None:
        get_voice_delegation_task_service().fail(execution.delegation, exc)
    if execution.claim is not None:
        execution.idempotency.abandon(execution.claim)


def _enforce_voice_policy(operation: str):
    from flask import g

    is_agent_auth = bool(getattr(g, "auth_payload", None))
    is_user_auth = bool(getattr(g, "user", None))
    decision = get_exposure_policy_service().evaluate_voice_access(
        cfg=current_app.config.get("AGENT_CONFIG", {}) or {},
        is_agent_auth=is_agent_auth,
        is_user_auth=is_user_auth,
        is_admin=bool(getattr(g, "is_admin", False)),
        operation=operation,
    )
    if decision.allowed:
        return None, decision.policy
    if decision.policy.get("emit_audit_events", True):
        actor, tenant_id = _audit_identity()
        log_audit(
            "voice_access_blocked",
            {
                "actor": actor,
                "tenant_id": tenant_id,
                "reason": decision.reason,
                "auth_source": decision.auth_source,
                "operation": operation,
                "policy_decision": "denied",
                "request_id": str(request.headers.get("X-Request-ID") or f"voice-policy-{uuid.uuid4().hex}"),
            },
        )
    return (
        api_response(
            status="error",
            code=403,
            data={"error": {"code": "policy_denied", "message": decision.reason, "retriable": False}},
        ),
        decision.policy,
    )


@voice_bp.route("/v1/voice/capabilities", methods=["GET"])
@_observe("capabilities")
@check_auth
def capabilities():
    blocked, _policy = _enforce_voice_policy("capabilities")
    if blocked:
        return blocked
    provider = get_voice_provider_service()
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

    correction_catalog = generative_corrector_capability_bundle(
        current_app.config.get("AGENT_CONFIG", {}) or {}
    )
    correction_models = correction_catalog["correction_models"]
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
    provider = get_voice_provider_service()
    audit_id = f"audit-voice-{uuid.uuid4()}"
    principal = _principal()
    profile_id = str(request.form.get("profile_id") or "default")
    configuration_session_id = (
        str(request.form.get("session_id") or request.form.get("configuration_session_id") or "").strip()
        or None
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
    admission_service = get_voice_admission_service()
    try:
        context = _recognition_context(
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
                artifact = get_voice_result_artifact_service().get(principal, result_ref)
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
        admission_limits = _voice_admission_limits()
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
        choice_applied = False
        choice_reason = "restricted_choice_disabled"
        choice_manifest_digest = ""
        corrector_applied = False
        corrector_reason = "generative_corrector_disabled"
        feature_flags = (
            effective_configuration.get("feature_flags") if isinstance(effective_configuration, dict) else None
        )
        if (
            isinstance(effective_configuration, dict)
            and effective_configuration.get("correction_policy") == "restricted_choice"
            and isinstance(feature_flags, dict)
            and feature_flags.get("restricted_worker") is True
        ):
            try:
                choice_outcome = get_voice_restricted_choice_service().apply(
                    result,
                    effective_configuration=effective_configuration,
                    tenant_id=principal.tenant_id,
                    task_id=delegation.task_id,
                    run_id=str(request.headers.get("X-Run-ID") or new_voice_choice_run_id()),
                    request_id=audit_id,
                    deadline_epoch_ms=delegation.deadline_epoch_ms,
                    policy_hash=voice_choice_policy_hash(effective_configuration),
                )
                result = choice_outcome.result
                choice_applied = choice_outcome.applied
                choice_reason = choice_outcome.reason_code
                choice_manifest_digest = choice_outcome.manifest_digest
            except Exception:
                # Strict fail-open-to-baseline semantics: provider result remains
                # the exact object returned above when the optional hook fails.
                choice_reason = "restricted_choice_hook_failed"
        elif (
            isinstance(effective_configuration, dict)
            and effective_configuration.get("correction_policy") == "generative_local"
            and isinstance(feature_flags, dict)
            and feature_flags.get("generative_judge") is True
        ):
            judge_outcome = get_voice_generative_judge_service().apply(
                result,
                effective_configuration=effective_configuration,
                tenant_id=principal.tenant_id,
                parent_task_id=delegation.task_id,
                request_id=audit_id,
                deadline_epoch_ms=delegation.deadline_epoch_ms,
            )
            result = judge_outcome.result
            choice_applied = judge_outcome.applied
            choice_reason = judge_outcome.reason_code
        elif (
            isinstance(effective_configuration, dict)
            and effective_configuration.get("correction_policy") == "generative_rewrite"
            and isinstance(feature_flags, dict)
            and feature_flags.get("generative_corrector") is True
        ):
            corrector_outcome = get_voice_generative_corrector_service().apply(
                result,
                effective_configuration=effective_configuration,
                tenant_id=principal.tenant_id,
                parent_task_id=delegation.task_id,
                request_id=audit_id,
                language=str(request.form.get("language") or "").strip() or None,
                deadline_epoch_ms=delegation.deadline_epoch_ms,
            )
            result = corrector_outcome.result
            corrector_applied = corrector_outcome.applied
            corrector_reason = corrector_outcome.reason_code
        artifact = get_voice_result_artifact_service().create(
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

    log_audit(
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


@voice_bp.route("/v1/voice/command", methods=["POST"])
@_observe("command")
@check_auth
def command():
    request_started_epoch_ms = time.time_ns() // 1_000_000
    blocked, _policy = _enforce_voice_policy("command")
    if blocked:
        return blocked
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
    context_value = request.form.get("command_context")
    parsed_context: dict[str, Any] | None = None
    if context_value:
        try:
            context_payload = json.loads(context_value)
            parsed_context = dict(context_payload) if isinstance(context_payload, Mapping) else None
        except ValueError:
            parsed_context = None
    try:
        provider = get_voice_provider_service()
        execution = _execute_hub_voice_request(
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
    log_audit(
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
        provider = get_voice_provider_service()
        execution = _execute_hub_voice_request(
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

    if execution.idempotent_replay and (
        execution.claim is None or execution.claim.replayed
    ):
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
        log_audit(
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
        log_audit(
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
    log_audit(
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
    try:
        deadline_seconds = max(1.0, min(float(body.get("deadline_seconds") or 120.0), 300.0))
    except (TypeError, ValueError):
        return api_response(
            status="error",
            code=422,
            data={"error": {"code": "voice_stream.invalid_deadline", "message": "deadline_seconds is invalid"}},
        )
    admission_limits = _voice_admission_limits()
    media_type = str(body.get("media_type") or "audio/pcm;rate=16000;channels=1")
    try:
        requested_audio_seconds = float(
            admission_limits.max_audio_seconds_per_request
            if body.get("max_audio_seconds") is None
            else body["max_audio_seconds"]
        )
        if requested_audio_seconds <= 0:
            raise ValueError("max_audio_seconds must be positive")
        max_audio_seconds = max(
            0.001,
            min(requested_audio_seconds, admission_limits.max_audio_seconds_per_request),
        )
        admission_audio_seconds = reserve_stream_audio_seconds(
            media_type=media_type,
            requested_audio_seconds=max_audio_seconds,
            max_audio_seconds=admission_limits.max_audio_seconds_per_request,
        )
    except (TypeError, ValueError):
        return api_response(
            status="error",
            code=422,
            data={"error": {"code": "voice_stream.invalid_audio_budget", "message": "max_audio_seconds is invalid"}},
        )
    payload: dict[str, Any] = {
        "filename": str(body.get("filename") or "stream.pcm")[:255],
        "language": str(body.get("language") or "").strip() or None,
        "profile_id": str(body.get("profile_id") or "default"),
        "configuration_session_id": str(body.get("configuration_session_id") or "").strip() or None,
        "media_type": media_type,
        "deadline_seconds": deadline_seconds,
        "max_audio_seconds": max_audio_seconds,
    }
    claim = None
    delegation: VoiceDelegationTask | None = None
    admission_lease: VoiceAdmissionLease | None = None
    runtime_session_id = ""
    hub_session_id = f"voice-stream-{uuid.uuid4().hex}"
    session = None
    stream_committed = False
    stream_request_id = ""
    admission_service = get_voice_admission_service()
    try:
        payload["profile_id"] = validate_identifier(payload["profile_id"], field="profile_id")
        recognition_context = _recognition_context(
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
        deadline_budget = min(payload["deadline_seconds"], configured_deadline)
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
            parent_task_id=None,
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
        runtime = get_voice_provider_service().create_stream(
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
        session = get_voice_stream_session_service().create(
            principal,
            runtime_session_id=runtime_session_id,
            session_id=hub_session_id,
            deadline_seconds=remaining_deadline,
            profile_id=payload["profile_id"],
            configuration_session_id=payload["configuration_session_id"],
            language=payload["language"],
            effective_configuration=(
                effective_configuration if isinstance(effective_configuration, dict) else {}
            ),
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
        )
        admission_service.release_concurrency(admission_lease)
        admission_lease = None  # The stream session now owns and releases the lease.
        idempotency.complete(claim, {"session_id": session.session_id, "task_id": delegation.task_id})
        stream_committed = True
        log_audit(
            "voice_stream_created",
            {
                "actor": principal.subject,
                "session_id": session.session_id,
                "tenant_id": principal.tenant_id,
                "operation": "stream",
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


@voice_bp.route("/v1/voice/streams/<session_id>/chunks/<int:chunk_sequence>", methods=["PUT"])
@_observe("stream")
@check_auth
def push_voice_stream_chunk(session_id: str, chunk_sequence: int):
    blocked, _policy = _enforce_voice_policy("stream")
    if blocked:
        return blocked
    chunk = request.stream.read((1024 * 1024) + 1)
    if not chunk or len(chunk) > 1024 * 1024:
        return api_response(
            status="error",
            code=413 if chunk else 422,
            data={"error": {"code": "voice_stream.invalid_chunk", "message": "chunk must contain at most 1MB"}},
        )
    principal = _principal()
    try:
        session_service = get_voice_stream_session_service()
        chunk_digest = hashlib.sha256(chunk).hexdigest()
        reservation = session_service.begin_chunk(
            principal,
            session_id,
            chunk_sequence=chunk_sequence,
            chunk_digest=chunk_digest,
            chunk_size=len(chunk),
        )
        session = reservation.session
        if reservation.replayed:
            replay_event = {
                "event_type": "chunk_replayed",
                "payload": {
                    "chunk_sequence": chunk_sequence,
                    "next_chunk_sequence": session.next_chunk_sequence,
                },
            }
            record_stream_event("chunk_replayed")
            return api_response(data={"stream": session.public(), "event": replay_event}, code=202)
        provider = get_voice_provider_service()
        try:
            runtime = provider.push_stream_chunk(
                runtime_session_id=session.runtime_session_id,
                chunk_sequence=chunk_sequence,
                content=chunk,
                request_id=session.request_id,
                deadline_seconds=max(0.001, session.deadline_at - time.time()),
            )
        except Exception:
            session_service.abort_chunk(
                principal,
                session_id,
                chunk_sequence=chunk_sequence,
                chunk_digest=chunk_digest,
            )
            raise
        try:
            session = session_service.complete_chunk(
                principal,
                session_id,
                chunk_sequence=chunk_sequence,
                chunk_digest=chunk_digest,
            )
        except Exception:
            session_service.abort_chunk(
                principal,
                session_id,
                chunk_sequence=chunk_sequence,
                chunk_digest=chunk_digest,
            )
            try:
                provider.delete_stream(
                    runtime_session_id=session.runtime_session_id,
                    request_id=session.request_id,
                    deadline_seconds=max(0.001, session.deadline_at - time.time()),
                )
            except Exception:
                pass
            raise
        event_raw = runtime.get("event")
        event_value: dict[str, Any] = event_raw if isinstance(event_raw, dict) else {}
        record_stream_event(event_value.get("event_type") or "ack")
        return api_response(data={"stream": session.public(), "event": runtime.get("event")}, code=202)
    except VoiceProviderError as exc:
        return _provider_error(exc)
    except VoiceGovernanceError as exc:
        return _governance_error(exc)


@voice_bp.route("/v1/voice/streams/<session_id>/finalize", methods=["POST"])
@_observe("stream")
@check_auth
def finalize_voice_stream(session_id: str):
    blocked, _policy = _enforce_voice_policy("stream")
    if blocked:
        return blocked
    principal = _principal()
    session = None
    finalize_token = ""
    try:
        session_service = get_voice_stream_session_service()
        finalize_reservation = session_service.begin_finalize(principal, session_id)
        session = finalize_reservation.session
        finalize_token = finalize_reservation.token
        runtime = get_voice_provider_service().finalize_stream(
            runtime_session_id=session.runtime_session_id,
            request_id=session.request_id,
            deadline_seconds=max(0.001, session.deadline_at - time.time()),
        )
        event_value = runtime.get("event")
        event: dict[str, Any] = event_value if isinstance(event_value, dict) else {}
        payload_value = event.get("payload")
        payload: dict[str, Any] = payload_value if isinstance(payload_value, dict) else {}
        result_value = payload.get("result")
        result: dict[str, Any] = result_value if isinstance(result_value, dict) else {}
        snapshot_value = json.loads(session.effective_configuration_json)
        effective_configuration = snapshot_value if isinstance(snapshot_value, dict) else {}
        feature_flags = (
            effective_configuration.get("feature_flags")
            if isinstance(effective_configuration, dict)
            else None
        )
        if (
            isinstance(effective_configuration, dict)
            and effective_configuration.get("correction_policy") == "generative_rewrite"
            and isinstance(feature_flags, dict)
            and feature_flags.get("generative_corrector") is True
        ):
            corrector_outcome = get_voice_generative_corrector_service().apply(
                result,
                effective_configuration=effective_configuration,
                tenant_id=principal.tenant_id,
                parent_task_id=session.task_id,
                request_id=session.request_id,
                language=session.language,
                deadline_epoch_ms=round(session.deadline_at * 1000),
            )
            result = corrector_outcome.result
            payload = {**payload, "result": result}
            event = {**event, "payload": payload}
        artifact = get_voice_result_artifact_service().create(
            principal,
            request_hash=hashlib.sha256(f"stream:{session.session_id}".encode()).hexdigest(),
            result=result,
            profile_id=session.profile_id,
        )
        session = session_service.complete_finalize(
            principal,
            session_id,
            token=finalize_token,
            result_ref=artifact["id"],
        )
        if session.task_id:
            get_voice_delegation_task_service().complete(
                VoiceDelegationTask(task_id=session.task_id, deadline_epoch_ms=0),
                result_ref=artifact["id"],
            )
        cleanup = get_voice_runtime_cleanup_service()
        cleanup.activate_target(
            principal,
            session.profile_id,
            session.session_id,
            operation="stream_orphan",
        )
        cleanup.retry_target(principal, session.profile_id, session.session_id)
        record_voice_result(result)
        record_stream_event("final")
        return api_response(
            data={"stream": session.public(), "result": result, "result_ref": artifact["id"], "event": event}
        )
    except VoiceProviderError as exc:
        if finalize_token:
            _fail_finalize_and_cleanup(principal, session_id, finalize_token)
        if session is not None and session.task_id:
            get_voice_delegation_task_service().fail(
                VoiceDelegationTask(task_id=session.task_id, deadline_epoch_ms=0),
                exc,
            )
        return _provider_error(exc)
    except VoiceGovernanceError as exc:
        if finalize_token:
            _fail_finalize_and_cleanup(principal, session_id, finalize_token)
        if session is not None and session.task_id:
            get_voice_delegation_task_service().fail(
                VoiceDelegationTask(task_id=session.task_id, deadline_epoch_ms=0),
                exc,
            )
        return _governance_error(exc)
    except Exception as exc:
        if finalize_token:
            _fail_finalize_and_cleanup(principal, session_id, finalize_token)
        if session is not None and session.task_id:
            get_voice_delegation_task_service().fail(
                VoiceDelegationTask(task_id=session.task_id, deadline_epoch_ms=0),
                exc,
            )
        raise


def _fail_finalize_and_cleanup(
    principal: VoicePrincipal,
    session_id: str,
    finalize_token: str,
) -> None:
    failed_session = get_voice_stream_session_service().fail_finalize(
        principal,
        session_id,
        token=finalize_token,
    )
    if failed_session is None:
        return
    cleanup = get_voice_runtime_cleanup_service()
    cleanup.activate_target(
        principal,
        failed_session.profile_id,
        failed_session.session_id,
        operation="stream_orphan",
    )
    cleanup.retry_target(principal, failed_session.profile_id, failed_session.session_id)


@voice_bp.route("/v1/voice/streams/<session_id>", methods=["GET"])
@_observe("stream")
@check_auth
def get_voice_stream(session_id: str):
    blocked, _policy = _enforce_voice_policy("stream")
    if blocked:
        return blocked
    principal = _principal()
    try:
        session = get_voice_stream_session_service().require(principal, session_id)
        after_event = int(request.args.get("after_event", -1))
        runtime = get_voice_provider_service().get_stream(
            runtime_session_id=session.runtime_session_id,
            after_event=after_event,
            request_id=session.request_id,
            deadline_seconds=max(0.001, session.deadline_at - time.time()),
        )
        return api_response(data={"stream": session.public(), "runtime": runtime})
    except (TypeError, ValueError):
        return api_response(
            status="error",
            code=400,
            data={"error": {"code": "voice_stream.invalid_cursor", "message": "after_event must be an integer"}},
        )
    except VoiceProviderError as exc:
        return _provider_error(exc)
    except VoiceGovernanceError as exc:
        return _governance_error(exc)


@voice_bp.route("/v1/voice/streams/<session_id>", methods=["DELETE"])
@_observe("stream")
@check_auth
def delete_voice_stream(session_id: str):
    blocked, _policy = _enforce_voice_policy("stream")
    if blocked:
        return blocked
    principal = _principal()
    try:
        session = get_voice_stream_session_service().require(principal, session_id)
        was_terminal = session.state in {"final", "failed", "closed"}
        cleanup = get_voice_runtime_cleanup_service()
        cleanup.activate_target(
            principal,
            session.profile_id,
            session.session_id,
            operation="stream_orphan",
        )
        get_voice_provider_service().delete_stream(
            runtime_session_id=session.runtime_session_id,
            request_id=session.request_id,
            deadline_seconds=max(0.001, session.deadline_at - time.time()),
        )
        cleanup.cancel_target(principal, session.profile_id, session.session_id)
        session = get_voice_stream_session_service().delete(principal, session_id)
        if session.task_id and not was_terminal:
            get_voice_delegation_task_service().cancel(
                session.task_id,
                reason_code="voice_stream_cancelled",
            )
        record_stream_event("cancelled")
        return api_response(data={"stream": session.public(), "deleted": True})
    except VoiceProviderError as exc:
        return _provider_error(exc)
    except VoiceGovernanceError as exc:
        return _governance_error(exc)
