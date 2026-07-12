from __future__ import annotations

import hashlib
import json
import time
import uuid

from flask import Blueprint, current_app, g, request

from agent.auth import check_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.services.exposure_policy_service import get_exposure_policy_service
from agent.services.restricted_inference_management_service import (
    RestrictedInferenceManagementError,
    get_restricted_inference_management_service,
)
from agent.services.restricted_inference_management_task_service import (
    get_restricted_inference_management_task_service,
)
from agent.services.voice_governance_domain import VoiceGovernanceError, VoicePrincipal
from agent.services.voice_idempotency_service import VoiceIdempotencyService

restricted_inference_management_bp = Blueprint("restricted_inference_management", __name__)


def _enforce(operation: str):
    decision = get_exposure_policy_service().evaluate_voice_access(
        cfg=current_app.config.get("AGENT_CONFIG", {}) or {},
        is_agent_auth=bool(getattr(g, "auth_payload", None)),
        is_user_auth=bool(getattr(g, "user", None)),
        is_admin=bool(getattr(g, "is_admin", False)),
        operation=operation,
    )
    if decision.allowed:
        return None
    return api_response(
        status="error",
        code=403,
        data={"error": {"code": decision.reason, "message": "voice management access denied", "retriable": False}},
    )


def _actor() -> tuple[str, str]:
    identity = dict(getattr(g, "user", {}) or getattr(g, "auth_payload", {}) or {})
    subject = str(identity.get("sub") or identity.get("username") or "unknown")
    tenant = str(identity.get("tenant_id") or identity.get("tenant") or subject)
    return subject, tenant


def _principal() -> VoicePrincipal:
    subject, tenant = _actor()
    return VoicePrincipal(tenant_id=tenant, subject=subject)


def _etag(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return f'"{hashlib.sha256(canonical).hexdigest()}"'


def _configuration_view(payload: dict) -> dict:
    return {key: payload[key] for key in ("fixed", "mutable", "schema_version", "version") if key in payload}


def _request_id(prefix: str) -> str:
    supplied = str(request.headers.get("X-Request-ID") or "").strip()
    if supplied and len(supplied) <= 128 and all(char.isalnum() or char in "-_.:" for char in supplied):
        return supplied
    return f"{prefix}-{uuid.uuid4().hex}"


def _idempotency_key(*, required: bool) -> str | None:
    key = str(request.headers.get("Idempotency-Key") or "").strip()
    if required and not key:
        raise VoiceGovernanceError(
            code="restricted_management.idempotency_key_required",
            message="Idempotency-Key header is required",
            status_code=400,
        )
    return key or None


def _governance_error(exc: VoiceGovernanceError):
    return api_response(
        status="error",
        code=exc.status_code,
        data={"error": {"code": exc.code, "message": exc.message, "retriable": False}},
    )


def _execute_mutation(
    *,
    operation: str,
    target_id: str,
    request_payload: dict,
    callback,
    require_idempotency: bool,
) -> dict:
    principal = _principal()
    key = _idempotency_key(required=require_idempotency)
    claim = None
    idempotency = VoiceIdempotencyService()
    if key:
        claim = idempotency.begin(
            principal,
            operation=f"restricted_management.{operation}:{target_id}",
            idempotency_key=key,
            payload=request_payload,
        )
        if claim.replayed:
            return {**dict(claim.result_metadata), "idempotent_replay": True}
    request_id = _request_id(f"restricted-{operation}")
    try:
        task_result = get_restricted_inference_management_task_service().execute(
            principal,
            operation=operation,
            target_id=target_id,
            request_id=request_id,
            callback=callback,
        )
        result = {
            **task_result.payload,
            "management_task_id": task_result.task_id,
            "idempotent_replay": False,
        }
        if claim is not None:
            idempotency.complete(claim, result)
        return result
    except Exception:
        if claim is not None:
            idempotency.abandon(claim)
        actor, tenant = _actor()
        log_audit(
            "restricted_inference_management_failed",
            {
                "actor": actor,
                "tenant_id": tenant,
                "operation": operation,
                "target_digest": hashlib.sha256(target_id.encode()).hexdigest(),
                "status": "failed",
            },
        )
        raise


def _error(exc: RestrictedInferenceManagementError):
    return api_response(
        status="error",
        code=exc.status_code,
        data={"error": {"code": exc.reason_code, "message": str(exc), "retriable": exc.status_code >= 500}},
    )


@restricted_inference_management_bp.get("/v1/voice/restricted-inference/status")
@check_auth
def restricted_inference_status():
    denied = _enforce("model_status")
    if denied:
        return denied
    try:
        payload = get_restricted_inference_management_service().status()
    except RestrictedInferenceManagementError as exc:
        return _error(exc)
    actor, tenant = _actor()
    log_audit(
        "restricted_inference_status_read",
        {"actor": actor, "tenant_id": tenant, "request_id": f"restricted-status-{uuid.uuid4().hex}"},
    )
    return api_response(data={"restricted_inference": payload})


@restricted_inference_management_bp.get("/v1/voice/restricted-inference/configuration")
@check_auth
def restricted_inference_configuration():
    denied = _enforce("model_config")
    if denied:
        return denied
    try:
        payload = get_restricted_inference_management_service().configuration()
    except RestrictedInferenceManagementError as exc:
        return _error(exc)
    actor, tenant = _actor()
    log_audit(
        "restricted_inference_configuration_read",
        {"actor": actor, "tenant_id": tenant, "version": int(payload.get("version") or 0)},
    )
    response, status_code = api_response(data={"restricted_inference": payload})
    response.headers["ETag"] = _etag(_configuration_view(payload))
    response.headers["Cache-Control"] = "no-store"
    return response, status_code


@restricted_inference_management_bp.patch("/v1/voice/restricted-inference/configuration")
@check_auth
def restricted_inference_configuration_update():
    denied = _enforce("model_config")
    if denied:
        return denied
    body = request.get_json(silent=True)
    delta = body.get("delta") if isinstance(body, dict) else None
    if (
        not isinstance(body, dict)
        or set(body) != {"delta"}
        or not isinstance(delta, dict)
        or set(delta) != {"allow_cpu_fallback"}
        or not isinstance(delta.get("allow_cpu_fallback"), bool)
    ):
        return api_response(
            status="error",
            code=422,
            data={
                "error": {
                    "code": "invalid_runtime_configuration",
                    "message": "configuration delta is invalid",
                    "retriable": False,
                }
            },
        )
    try:
        _idempotency_key(required=True)
        supplied_etag = str(request.headers.get("If-Match") or "").strip()
        service = get_restricted_inference_management_service()

        def update_configuration() -> dict:
            current = service.configuration()
            expected_etag = _etag(_configuration_view(current))
            if supplied_etag != expected_etag:
                raise RestrictedInferenceManagementError(
                    "restricted_management.etag_mismatch",
                    "runtime configuration changed; refresh before updating",
                    status_code=412,
                )
            return service.update_configuration(
                delta,
                expected_version=int(current.get("version") or 0),
            )

        payload = _execute_mutation(
            operation="model_config",
            target_id="runtime",
            request_payload={"delta": delta, "etag": supplied_etag},
            callback=update_configuration,
            require_idempotency=True,
        )
    except VoiceGovernanceError as exc:
        return _governance_error(exc)
    except RestrictedInferenceManagementError as exc:
        return _error(exc)
    actor, tenant = _actor()
    log_audit(
        "restricted_inference_configuration_updated",
        {
            "actor": actor,
            "tenant_id": tenant,
            "field_names": sorted(body["delta"]) if isinstance(body.get("delta"), dict) else [],
            "version": int(payload.get("version") or 0),
            "management_task_id": payload.get("management_task_id"),
            "idempotent_replay": bool(payload.get("idempotent_replay")),
        },
    )
    response, status_code = api_response(data={"restricted_inference": payload})
    response.headers["ETag"] = _etag(_configuration_view(payload))
    response.headers["Cache-Control"] = "no-store"
    return response, status_code


@restricted_inference_management_bp.post("/v1/voice/restricted-inference/models/<manifest_id>/load")
@check_auth
def restricted_inference_load(manifest_id: str):
    denied = _enforce("model_load")
    if denied:
        return denied
    raw_body = request.get_json(silent=True)
    body = {} if raw_body is None else raw_body
    if not isinstance(body, dict) or set(body) - {"deadline_seconds"}:
        return api_response(
            status="error",
            code=422,
            data={"error": {"code": "invalid_load_request", "message": "load request is invalid"}},
        )
    try:
        deadline_seconds = max(1.0, min(float(body.get("deadline_seconds") or 120.0), 300.0))
        deadline_epoch_ms = time.time_ns() // 1_000_000 + round(deadline_seconds * 1000)
        service = get_restricted_inference_management_service()
        payload = _execute_mutation(
            operation="model_load",
            target_id=manifest_id,
            request_payload={"manifest_id": manifest_id, "deadline_seconds": deadline_seconds},
            callback=lambda: service.load(manifest_id, deadline_epoch_ms=deadline_epoch_ms),
            require_idempotency=True,
        )
    except (TypeError, ValueError):
        return api_response(
            status="error",
            code=422,
            data={"error": {"code": "invalid_load_request", "message": "load deadline is invalid"}},
        )
    except VoiceGovernanceError as exc:
        return _governance_error(exc)
    except RestrictedInferenceManagementError as exc:
        return _error(exc)
    actor, tenant = _actor()
    log_audit(
        "restricted_inference_model_loaded",
        {
            "actor": actor,
            "tenant_id": tenant,
            "manifest_id": manifest_id,
            "management_task_id": payload.get("management_task_id"),
            "idempotent_replay": bool(payload.get("idempotent_replay")),
        },
    )
    return api_response(data={"restricted_inference": payload})


@restricted_inference_management_bp.post("/v1/voice/restricted-inference/models/<manifest_digest>/unload")
@check_auth
def restricted_inference_unload(manifest_digest: str):
    denied = _enforce("model_unload")
    if denied:
        return denied
    try:
        service = get_restricted_inference_management_service()
        payload = _execute_mutation(
            operation="model_unload",
            target_id=manifest_digest,
            request_payload={"manifest_digest": manifest_digest},
            callback=lambda: service.unload(manifest_digest),
            require_idempotency=False,
        )
    except VoiceGovernanceError as exc:
        return _governance_error(exc)
    except RestrictedInferenceManagementError as exc:
        return _error(exc)
    actor, tenant = _actor()
    log_audit(
        "restricted_inference_model_unloaded",
        {
            "actor": actor,
            "tenant_id": tenant,
            "manifest_digest": manifest_digest,
            "unloaded": bool(payload.get("unloaded")),
        },
    )
    return api_response(data={"restricted_inference": payload})


@restricted_inference_management_bp.post("/v1/voice/restricted-inference/cache/gc")
@check_auth
def restricted_inference_cache_gc():
    denied = _enforce("model_cache_gc")
    if denied:
        return denied
    try:
        service = get_restricted_inference_management_service()
        payload = _execute_mutation(
            operation="model_cache_gc",
            target_id="runtime-cache",
            request_payload={},
            callback=service.cache_gc,
            require_idempotency=False,
        )
    except VoiceGovernanceError as exc:
        return _governance_error(exc)
    except RestrictedInferenceManagementError as exc:
        return _error(exc)
    actor, tenant = _actor()
    log_audit(
        "restricted_inference_cache_gc",
        {
            "actor": actor,
            "tenant_id": tenant,
            "removed_entries": int(payload.get("removed_entries") or 0),
        },
    )
    return api_response(data={"restricted_inference": payload})


@restricted_inference_management_bp.post("/v1/voice/restricted-inference/models/download")
@check_auth
def restricted_inference_download_disabled():
    denied = _enforce("model_download")
    if denied:
        return denied
    actor, tenant = _actor()
    log_audit(
        "restricted_inference_model_download_blocked",
        {"actor": actor, "tenant_id": tenant, "reason_code": "offline_download_forbidden"},
    )
    return api_response(
        status="error",
        code=409,
        data={
            "error": {
                "code": "offline_download_forbidden",
                "message": "production model downloads are disabled; promote an immutable offline snapshot",
                "retriable": False,
            }
        },
    )
