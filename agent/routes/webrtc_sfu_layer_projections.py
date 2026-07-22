"""Authenticated conditional-read and receipt API for signed layer projections."""

from __future__ import annotations

import hashlib
import time

from flask import Blueprint, current_app, jsonify, request

from agent.auth import check_user_auth, get_request_auth_context
from agent.common.audit import log_audit
from agent.services.sfu_layer_projection_service import SfuLayerProjectionError, SfuLayerProjectionService


webrtc_sfu_layer_projections_bp = Blueprint("webrtc_sfu_layer_projections", __name__)


@webrtc_sfu_layer_projections_bp.get("/v1/semantic-media/sfu/rooms/<room_ref>/projections/room")
@check_user_auth
def read_room_projection(room_ref: str):
    return _read(room_ref, "room", room_ref)


@webrtc_sfu_layer_projections_bp.get("/v1/semantic-media/sfu/rooms/<room_ref>/projections/publishers/<publication_ref>")
@check_user_auth
def read_publisher_projection(room_ref: str, publication_ref: str):
    return _read(room_ref, "publisher", publication_ref)


@webrtc_sfu_layer_projections_bp.get("/v1/semantic-media/sfu/rooms/<room_ref>/projections/receivers/<subscription_ref>")
@check_user_auth
def read_receiver_projection(room_ref: str, subscription_ref: str):
    return _read(room_ref, "receiver", subscription_ref)


@webrtc_sfu_layer_projections_bp.post("/v1/semantic-media/sfu/rooms/<room_ref>/projection-receipts/<projection_ref>")
@check_user_auth
def submit_projection_receipt(room_ref: str, projection_ref: str):
    try:
        actor, tenant = _identity()
        scope = _scope(tenant, room_ref, actor, "receiver", projection_ref)
        actor_digest = hashlib.sha256(f"{tenant}\0{room_ref}\0{actor}".encode()).hexdigest()
        _service().record_receipt(
            scope=scope, projection_ref=projection_ref, actor_digest=actor_digest,
            raw_document=request.get_data(cache=False, as_text=False),
        )
    except SfuLayerProjectionError as exc:
        return _failure(exc)
    _audit("accepted", "sfu_projection_receipt_saved")
    return jsonify({"ok": True, "reason_code": "sfu_projection_receipt_saved"}), 202


def _read(room_ref: str, kind: str, subject_ref: str):
    try:
        actor, tenant = _identity()
        scope = _scope(tenant, room_ref, actor, kind, subject_ref)
        cursor = _bounded_int("cursor", 0, 2_147_483_647, default=0)
        wait_ms = _bounded_int("wait_ms", 0, 1500, default=0)
        deadline = time.monotonic() + wait_ms / 1000.0
        projection = _service().read(scope=scope, projection_kind=kind, subject_ref=subject_ref, cursor=cursor)
        if projection is None and wait_ms and time.monotonic() < deadline:
            time.sleep(max(0.0, deadline - time.monotonic()))
            projection = _service().read(scope=scope, projection_kind=kind, subject_ref=subject_ref, cursor=cursor)
        if projection is None:
            response = jsonify({"ok": True, "status": "heartbeat", "cursor": cursor, "safe_outcome": "ordinary_fallback"})
            response.headers["Cache-Control"] = "no-store"
            return response, 200
    except SfuLayerProjectionError as exc:
        return _failure(exc)
    etag = f'"{projection.payload_digest}"'
    if request.headers.get("If-None-Match") == etag:
        return "", 304, {"ETag": etag, "Cache-Control": "no-store"}
    response = jsonify({
        "ok": True, "status": "projection", "cursor": projection.projection_version,
        "projection_digest": projection.payload_digest, "signature": projection.signature,
        "signature_key_id": projection.signature_key_id,
        "signature_algorithm": projection.signature_algorithm,
        "signature_algorithm_version": projection.signature_algorithm_version,
        "signature_key_version": projection.signature_key_version,
        "document": projection.payload,
    })
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "no-store"
    return response, 200


def _scope(tenant: str, room: str, actor: str, kind: str, subject: str):
    authorizer = current_app.extensions.get("sfu_layer_projection_scope_authorizer")
    if authorizer is None or not callable(getattr(authorizer, "authorize", None)):
        raise SfuLayerProjectionError("sfu_projection_scope_unavailable", 503)
    scope = authorizer.authorize(
        tenant_id=tenant, room_id=room, actor_id=actor,
        projection_kind=kind, subject_ref=subject,
    )
    if scope is None:
        raise SfuLayerProjectionError("sfu_projection_scope_denied", 403)
    return scope


def _identity() -> tuple[str, str]:
    auth = dict(get_request_auth_context() or {})
    actor = str(auth.get("sub") or auth.get("username") or "").strip()
    tenant = str(auth.get("tenant_id") or auth.get("tenant") or "").strip()
    if not actor or not tenant:
        raise SfuLayerProjectionError("sfu_projection_identity_invalid", 401)
    return actor, tenant


def _bounded_int(name: str, minimum: int, maximum: int, *, default: int) -> int:
    raw = request.args.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise SfuLayerProjectionError(f"sfu_projection_{name}_invalid") from exc
    if value < minimum or value > maximum:
        raise SfuLayerProjectionError(f"sfu_projection_{name}_invalid")
    return value


def _service() -> SfuLayerProjectionService:
    service = current_app.extensions.get("sfu_layer_projection_service")
    if not isinstance(service, SfuLayerProjectionService):
        raise SfuLayerProjectionError("sfu_projection_service_unavailable", 503)
    return service


def _failure(exc: SfuLayerProjectionError):
    response = jsonify({"ok": False, "reason_code": exc.reason_code, "safe_outcome": "ordinary_fallback"})
    if exc.retry_after_seconds is not None:
        response.headers["Retry-After"] = str(exc.retry_after_seconds)
    _audit("denied", exc.reason_code)
    return response, exc.status_code


def _audit(outcome: str, reason_code: str) -> None:
    log_audit("sfu_layer_projection", {"outcome": outcome, "reason_code": reason_code})


__all__ = ["webrtc_sfu_layer_projections_bp"]
