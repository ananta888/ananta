"""Voice request parsing, identity, error envelopes, observation and policy guard."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from functools import wraps
from typing import (
    Any,
    Mapping,
)

from flask import (
    current_app,
    g,
    request,
)

from agent.common.errors import api_response
from agent.services.voice_admission_service import VoiceAdmissionLimits
from agent.services.voice_governance_domain import (
    VoiceGovernanceError,
    VoicePrincipal,
)
from agent.services.voice_observability import record_voice_request
from agent.services.voice_provider import VoiceProviderError


def _voice_module():
    """Resolve monkeypatch seams through the public ``agent.routes.voice`` module at call time.

    Tests patch collaborators such as service getters on ``agent.routes.voice``;
    looking them up lazily keeps those patches effective for code that
    now lives in sibling modules (and avoids an import-time cycle).
    """
    import importlib

    return importlib.import_module("agent.routes.voice")


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


def _store_audio_enabled() -> bool:
    app_cfg = _mapping(current_app.config.get("AGENT_CONFIG"))
    voice_cfg = _mapping(app_cfg.get("voice_runtime"))
    return bool(voice_cfg.get("store_audio"))


def _voice_privacy_state() -> dict:
    # Raw audio persistence is intentionally fail-closed until explicit storage wiring exists.
    return {
        "store_audio_requested": bool(_voice_module()._store_audio_enabled()),
        "store_audio_effective": False,
        "effective_audio_retention": "none",
        "policy_hint": "raw_audio_persistence_not_wired",
        "raw_audio_persisted": False,
        "raw_audio_persisted_after_request": False,
        "transient_request_spooling": True,
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


def _enforce_voice_policy(operation: str):
    from flask import g

    is_agent_auth = bool(getattr(g, "auth_payload", None))
    is_user_auth = bool(getattr(g, "user", None))
    decision = _voice_module().get_exposure_policy_service().evaluate_voice_access(
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
        _voice_module().log_audit(
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
