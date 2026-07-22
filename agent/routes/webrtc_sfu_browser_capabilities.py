"""Authenticated browser boundary for coarse, non-authoritative capabilities."""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from agent.auth import check_user_auth, get_request_auth_context
from agent.common.audit import log_audit
from agent.services.sfu_browser_capability_ingestion_service import (
    SfuBrowserCapabilityCommand,
    SfuBrowserCapabilityError,
    SfuBrowserCapabilityIngestionService,
)


webrtc_sfu_browser_capabilities_bp = Blueprint("webrtc_sfu_browser_capabilities", __name__)


@webrtc_sfu_browser_capabilities_bp.post("/v1/semantic-media/sfu/rooms/<room_ref>/browser-capabilities")
@check_user_auth
def ingest_browser_capability(room_ref: str):
    try:
        actor, tenant = _identity()
        scope = _scope(tenant, room_ref, actor)
        snapshot = _service().ingest(SfuBrowserCapabilityCommand(
            request.get_data(cache=False, as_text=False), scope, _expected_version(),
        ))
    except SfuBrowserCapabilityError as exc:
        response = jsonify({"ok": False, "reason_code": exc.reason_code})
        if exc.retry_after_seconds is not None:
            response.headers["Retry-After"] = str(exc.retry_after_seconds)
        _audit("denied", exc.reason_code)
        return response, exc.status_code
    _audit("accepted", "sfu_capability_saved")
    return jsonify(_public(snapshot)), 202


@webrtc_sfu_browser_capabilities_bp.get("/v1/semantic-media/sfu/rooms/<room_ref>/browser-capabilities/<pseudonym>")
@check_user_auth
def read_browser_capability(room_ref: str, pseudonym: str):
    try:
        actor, tenant = _identity()
        snapshot = _service().read(scope=_scope(tenant, room_ref, actor), browser_pseudonym=pseudonym)
    except SfuBrowserCapabilityError as exc:
        return jsonify({"ok": False, "reason_code": exc.reason_code}), exc.status_code
    return jsonify(_public(snapshot)), 200


@webrtc_sfu_browser_capabilities_bp.delete("/v1/semantic-media/sfu/rooms/<room_ref>/browser-capabilities/<pseudonym>")
@check_user_auth
def revoke_browser_capability(room_ref: str, pseudonym: str):
    try:
        actor, tenant = _identity()
        snapshot = _service().revoke(
            scope=_scope(tenant, room_ref, actor), browser_pseudonym=pseudonym,
            expected_version=_expected_version(required=True),
        )
    except SfuBrowserCapabilityError as exc:
        return jsonify({"ok": False, "reason_code": exc.reason_code}), exc.status_code
    _audit("revoked", "sfu_capability_revoked")
    return jsonify({"ok": True, "state": "unknown", "version": snapshot.version if snapshot else 0}), 200


def _scope(tenant: str, room: str, actor: str):
    resolver = current_app.extensions.get("sfu_capability_admission_scope")
    if resolver is None or not callable(getattr(resolver, "resolve", None)):
        raise SfuBrowserCapabilityError("sfu_capability_scope_unavailable", 503)
    scope = resolver.resolve(tenant_id=tenant, room_id=room, actor_id=actor)
    if scope is None:
        raise SfuBrowserCapabilityError("sfu_capability_scope_denied", 403)
    return scope


def _identity() -> tuple[str, str]:
    auth = dict(get_request_auth_context() or {})
    actor = str(auth.get("sub") or auth.get("username") or "").strip()
    tenant = str(auth.get("tenant_id") or auth.get("tenant") or "").strip()
    if not actor or not tenant:
        raise SfuBrowserCapabilityError("sfu_capability_identity_invalid", 401)
    return actor, tenant


def _expected_version(*, required: bool = False) -> int:
    raw = request.headers.get("If-Match")
    if raw is None and not required:
        return 0
    try:
        value = int(str(raw).strip('"'))
    except (TypeError, ValueError) as exc:
        raise SfuBrowserCapabilityError("sfu_capability_expected_version_invalid", 428) from exc
    if value < 0:
        raise SfuBrowserCapabilityError("sfu_capability_expected_version_invalid", 428)
    return value


def _service() -> SfuBrowserCapabilityIngestionService:
    service = current_app.extensions.get("sfu_browser_capability_ingestion_service")
    if not isinstance(service, SfuBrowserCapabilityIngestionService):
        raise SfuBrowserCapabilityError("sfu_capability_service_unavailable", 503)
    return service


def _public(snapshot) -> dict:
    return {
        "ok": True, "state": snapshot.state, "capability_class": snapshot.capability_class,
        "capability_version": snapshot.capability_version, "sequence": snapshot.sequence,
        "version": snapshot.version, "expires_at_ms": snapshot.expires_at_ms,
        "authorization_effect": "none", "reevaluation_required": snapshot.state == "active",
    }


def _audit(outcome: str, reason_code: str) -> None:
    log_audit("sfu_browser_capability", {"outcome": outcome, "reason_code": reason_code, "authorization_effect": "none"})


__all__ = ["webrtc_sfu_browser_capabilities_bp"]
