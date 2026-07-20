"""User-authenticated Hub routes for optional SFU admission."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from flask import Blueprint, current_app, jsonify, request

from agent.auth import check_user_auth, get_request_auth_context
from agent.common.audit import log_audit
from agent.services.semantic_media_audit_service import SemanticMediaAuditRecorder
from agent.services.semantic_sfu_admission_service import (
    SemanticSfuAdmissionService,
    SfuAdmissionError,
    get_semantic_sfu_admission_service,
)
from agent.services.semantic_sfu_group_key_service import (
    SemanticSfuGroupKeyService,
    SfuGroupKeyError,
    get_semantic_sfu_group_key_service,
)

semantic_sfu_admission_bp = Blueprint("semantic_sfu_admission", __name__)
_MAX_BODY_BYTES = 32 * 1024


def _identity() -> tuple[str, str]:
    auth = dict(get_request_auth_context() or {})
    actor = str(auth.get("sub") or auth.get("username") or "").strip()
    tenant = str(auth.get("tenant_id") or auth.get("tenant") or "default").strip()
    if not actor or not tenant or len(actor.encode()) > 128 or len(tenant.encode()) > 128:
        raise SfuAdmissionError("sfu_identity_invalid", 401)
    return actor, tenant


def _body() -> Mapping[str, Any]:
    if request.content_length is not None and request.content_length > _MAX_BODY_BYTES:
        raise SfuAdmissionError("sfu_request_too_large", 413)
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise SfuAdmissionError("sfu_request_invalid", 400)
    return value


def _query_session_epoch() -> tuple[str, int]:
    session_id = str(request.args.get("session_id") or "")
    raw_epoch = str(request.args.get("membership_epoch") or "")
    try:
        epoch = int(raw_epoch)
    except ValueError as exc:
        raise SfuAdmissionError("sfu_membership_epoch_invalid") from exc
    return session_id, epoch


def _handle(operation: str, callback: Callable[[SemanticSfuAdmissionService, Mapping[str, Any], str, str], dict]):
    try:
        actor, tenant = _identity()
        body = _body()
        result = callback(get_semantic_sfu_admission_service(), body, actor, tenant)
    except SfuAdmissionError as exc:
        log_audit(
            "semantic_sfu_admission_denied",
            {"operation": operation, "reason_code": exc.reason_code},
        )
        return jsonify({"ok": False, "error": exc.reason_code}), exc.status_code
    log_audit(
        "semantic_sfu_admission_granted",
        {
            "operation": operation,
            **_safe_scope_field(tenant, result.get("room_id")),
            "reason_code": str(result.get("reason_code") or "accepted"),
            "membership_epoch": result.get("membership_epoch"),
            "revision": result.get("revision"),
        },
    )
    return jsonify(result), 200


def _handle_group_key(
    operation: str,
    callback: Callable[[SemanticSfuGroupKeyService, Mapping[str, Any], str, str], dict],
):
    try:
        actor, tenant = _identity()
        body = _body()
        result = callback(get_semantic_sfu_group_key_service(), body, actor, tenant)
    except (SfuAdmissionError, SfuGroupKeyError) as exc:
        log_audit(
            "semantic_sfu_group_key_denied",
            {"operation": operation, "reason_code": exc.reason_code},
        )
        return jsonify({"ok": False, "error": exc.reason_code}), exc.status_code
    log_audit(
        "semantic_sfu_group_key_accepted",
        {
            "operation": operation,
            "reason_code": "accepted",
            "membership_epoch": result.get("membership_epoch")
            or dict(result.get("authorization") or {}).get("membership_epoch"),
            "group_key_epoch": result.get("group_key_epoch")
            or dict(result.get("authorization") or {}).get("epoch"),
        },
    )
    return jsonify(result), 200


def _safe_scope_field(tenant_id: str, room_id: object) -> dict[str, str]:
    recorder = current_app.extensions.get("semantic_media_audit_recorder")
    if not isinstance(recorder, SemanticMediaAuditRecorder) or not room_id:
        return {}
    return {
        "scope_digest": recorder.digest(
            "sfu-room",
            f"{tenant_id}:{room_id}",
        )
    }


@semantic_sfu_admission_bp.post("/v1/semantic-media/sfu/admissions/join")
@check_user_auth
def join_sfu():
    return _handle("join", lambda service, body, actor, tenant: service.join(body, actor_id=actor, tenant_id=tenant))


@semantic_sfu_admission_bp.get("/v1/semantic-media/sfu/admissions/state")
@check_user_auth
def read_sfu_state():
    try:
        actor, tenant = _identity()
        session_id, epoch = _query_session_epoch()
        result = get_semantic_sfu_admission_service().read_state(
            session_id=session_id,
            membership_epoch=epoch,
            actor_id=actor,
            tenant_id=tenant,
        )
    except SfuAdmissionError as exc:
        return jsonify({"ok": False, "error": exc.reason_code}), exc.status_code
    return jsonify(result), 200


@semantic_sfu_admission_bp.post("/v1/semantic-media/sfu/admissions/publications")
@check_user_auth
def authorize_publication():
    return _handle(
        "publish",
        lambda service, body, actor, tenant: service.authorize_publication(body, actor_id=actor, tenant_id=tenant),
    )


@semantic_sfu_admission_bp.post("/v1/semantic-media/sfu/admissions/subscriptions")
@check_user_auth
def authorize_subscription():
    return _handle(
        "subscribe",
        lambda service, body, actor, tenant: service.authorize_subscription(body, actor_id=actor, tenant_id=tenant),
    )


@semantic_sfu_admission_bp.post("/v1/semantic-media/sfu/admissions/leave")
@check_user_auth
def leave_sfu():
    return _handle("leave", lambda service, body, actor, tenant: service.leave(body, actor_id=actor, tenant_id=tenant))


@semantic_sfu_admission_bp.post("/v1/semantic-media/sfu/group-keys/epochs")
@check_user_auth
def prepare_sfu_group_key_epoch():
    return _handle_group_key(
        "prepare",
        lambda service, body, actor, tenant: service.prepare_epoch(body, actor_id=actor, tenant_id=tenant),
    )


@semantic_sfu_admission_bp.post(
    "/v1/semantic-media/sfu/group-keys/epochs/<authorization_id>/packages"
)
@check_user_auth
def deliver_sfu_group_key_packages(authorization_id: str):
    return _handle_group_key(
        "deliver",
        lambda service, body, actor, tenant: service.deliver_packages(
            authorization_id, body, actor_id=actor, tenant_id=tenant
        ),
    )


@semantic_sfu_admission_bp.get("/v1/semantic-media/sfu/group-keys/packages")
@check_user_auth
def read_sfu_group_key_packages():
    try:
        actor, tenant = _identity()
        session_id, epoch = _query_session_epoch()
        result = get_semantic_sfu_group_key_service().read_packages(
            session_id=session_id,
            membership_epoch=epoch,
            cursor=str(request.args.get("cursor") or ""),
            actor_id=actor,
            tenant_id=tenant,
        )
    except (SfuAdmissionError, SfuGroupKeyError) as exc:
        return jsonify({"ok": False, "error": exc.reason_code}), exc.status_code
    return jsonify(result), 200


@semantic_sfu_admission_bp.post(
    "/v1/semantic-media/sfu/group-keys/epochs/<authorization_id>/ack"
)
@check_user_auth
def acknowledge_sfu_group_key_package(authorization_id: str):
    return _handle_group_key(
        "ack",
        lambda service, body, actor, tenant: service.acknowledge_package(
            authorization_id, body, actor_id=actor, tenant_id=tenant
        ),
    )


@semantic_sfu_admission_bp.get(
    "/v1/semantic-media/sfu/group-keys/epochs/<authorization_id>"
)
@check_user_auth
def read_sfu_group_key_epoch_status(authorization_id: str):
    try:
        actor, tenant = _identity()
        result = get_semantic_sfu_group_key_service().epoch_status(
            authorization_id, actor_id=actor, tenant_id=tenant
        )
    except (SfuAdmissionError, SfuGroupKeyError) as exc:
        return jsonify({"ok": False, "error": exc.reason_code}), exc.status_code
    return jsonify(result), 200


__all__ = ["semantic_sfu_admission_bp"]
