"""Admin-only HTTP boundary for Hub-owned SFU runtime identities."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from flask import Blueprint, current_app, g, request

from agent.auth import admin_required
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.services.sfu_node_identity_service import (
    SfuNodeCredentialCommand,
    SfuNodeIdentityError,
    SfuNodeIdentityService,
    SfuNodeRevocationCommand,
    SfuProofOfPossession,
    assert_no_private_key_material,
)

webrtc_sfu_node_enrollment_bp = Blueprint("webrtc_sfu_node_enrollment", __name__)

_CREDENTIAL_FIELDS = frozenset(
    {
        "node_id",
        "runtime_control_mode",
        "roles",
        "public_key_pem",
        "credential_kind",
        "credential_fingerprint",
        "certificate_pem",
        "proof",
        "expected_version",
        "actor",
        "reason",
    }
)
_PROOF_FIELDS = frozenset({"algorithm", "signature", "nonce", "issued_at"})
_REVOKE_FIELDS = frozenset({"expected_version", "emergency", "actor", "reason"})


@webrtc_sfu_node_enrollment_bp.post("/api/admin/webrtc/sfu-nodes/enroll")
@admin_required
def enroll_sfu_node():
    return _credential_mutation("enroll")


@webrtc_sfu_node_enrollment_bp.post("/api/admin/webrtc/sfu-nodes/<node_id>/rotate")
@admin_required
def rotate_sfu_node(node_id: str):
    return _credential_mutation("rotate", path_node_id=node_id)


@webrtc_sfu_node_enrollment_bp.post("/api/admin/webrtc/sfu-nodes/<node_id>/revoke")
@admin_required
def revoke_sfu_node(node_id: str):
    actor = _authenticated_actor()
    reason = ""
    try:
        body = _json_body(_REVOKE_FIELDS)
        actor = _validated_actor(body)
        reason = str(body.get("reason") or "")
        result = _service().revoke(
            SfuNodeRevocationCommand(
                node_id=node_id,
                expected_version=_integer(body.get("expected_version"), "sfu_identity_expected_version_invalid"),
                emergency=_boolean(body.get("emergency", False), "sfu_identity_emergency_invalid"),
                actor=actor,
                reason=reason,
                idempotency_key=_idempotency_key(),
            )
        )
    except SfuNodeIdentityError as exc:
        _audit("revoke", node_id, actor, reason, "denied", exc.reason_code)
        return _error(exc)
    _audit("revoke", node_id, actor, reason, result.status, None)
    return api_response(data={"identity": result.identity.payload(), "result_status": result.status})


@webrtc_sfu_node_enrollment_bp.get("/api/admin/webrtc/sfu-nodes/<node_id>")
@admin_required
def get_sfu_node_identity(node_id: str):
    try:
        identity = _service().get(node_id)
    except SfuNodeIdentityError as exc:
        return _error(exc)
    return api_response(data={"identity": identity.payload()})


def _credential_mutation(operation: str, *, path_node_id: str | None = None):
    actor = _authenticated_actor()
    reason = ""
    node_id = path_node_id or "unknown"
    try:
        body = _json_body(_CREDENTIAL_FIELDS - ({"node_id"} if path_node_id else set()))
        actor = _validated_actor(body)
        reason = str(body.get("reason") or "")
        node_id = path_node_id or str(body.get("node_id") or "")
        proof_raw = body.get("proof")
        if not isinstance(proof_raw, Mapping) or set(proof_raw) - _PROOF_FIELDS:
            raise SfuNodeIdentityError("sfu_proof_invalid")
        roles_raw = body.get("roles")
        if not isinstance(roles_raw, list):
            raise SfuNodeIdentityError("sfu_identity_role_invalid")
        command = SfuNodeCredentialCommand(
            node_id=node_id,
            runtime_control_mode=str(body.get("runtime_control_mode") or ""),
            roles=tuple(str(role) for role in roles_raw),
            public_key_pem=str(body.get("public_key_pem") or ""),
            credential_kind=str(body.get("credential_kind") or ""),
            credential_fingerprint=(
                str(body["credential_fingerprint"]).lower()
                if body.get("credential_fingerprint") is not None
                else None
            ),
            certificate_pem=(str(body["certificate_pem"]) if body.get("certificate_pem") else None),
            proof=SfuProofOfPossession(
                algorithm=str(proof_raw.get("algorithm") or ""),
                signature=str(proof_raw.get("signature") or ""),
                nonce=str(proof_raw.get("nonce") or ""),
                issued_at=_number(proof_raw.get("issued_at"), "sfu_proof_issued_at_invalid"),
            ),
            expected_version=_integer(body.get("expected_version"), "sfu_identity_expected_version_invalid"),
            actor=actor,
            reason=reason,
            idempotency_key=_idempotency_key(),
        )
        result = (
            _service().enroll(command, source=request.remote_addr or "unknown")
            if operation == "enroll"
            else _service().rotate(command)
        )
    except SfuNodeIdentityError as exc:
        _audit(operation, node_id, actor, reason, "denied", exc.reason_code)
        return _error(exc)
    _audit(operation, node_id, actor, reason, result.status, None)
    return api_response(
        data={"identity": result.identity.payload(), "result_status": result.status},
        code=201 if result.status == "created" else 200,
    )


def _json_body(allowed_fields: frozenset[str] | set[str]) -> dict[str, Any]:
    body: Any = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise SfuNodeIdentityError("sfu_identity_json_object_required")
    assert_no_private_key_material(body)
    if set(body) - set(allowed_fields):
        raise SfuNodeIdentityError("sfu_identity_unknown_field")
    return body


def _validated_actor(body: Mapping[str, object]) -> str:
    actor = str(body.get("actor") or "").strip()
    if not actor:
        raise SfuNodeIdentityError("sfu_identity_actor_required")
    if actor != _authenticated_actor():
        raise SfuNodeIdentityError("sfu_identity_actor_mismatch", status_code=403)
    return actor


def _authenticated_actor() -> str:
    identity = getattr(g, "user", None)
    if not isinstance(identity, Mapping) or not identity:
        identity = getattr(g, "auth_payload", None)
    identity = identity if isinstance(identity, Mapping) else {}
    return str(
        identity.get("sub")
        or identity.get("username")
        or identity.get("agent_id")
        or identity.get("client_id")
        or "hub-admin"
    ).strip()


def _idempotency_key() -> str:
    key = str(request.headers.get("Idempotency-Key") or "").strip()
    if not key:
        raise SfuNodeIdentityError("sfu_identity_idempotency_key_required")
    return key


def _integer(value: object, reason_code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SfuNodeIdentityError(reason_code)
    return value


def _number(value: object, reason_code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SfuNodeIdentityError(reason_code)
    return float(value)


def _boolean(value: object, reason_code: str) -> bool:
    if not isinstance(value, bool):
        raise SfuNodeIdentityError(reason_code)
    return value


def _service() -> SfuNodeIdentityService:
    service = current_app.extensions.get("sfu_node_identity_service")
    if not isinstance(service, SfuNodeIdentityService):
        raise SfuNodeIdentityError("sfu_node_identity_service_unavailable", status_code=503)
    return service


def _audit(
    operation: str,
    node_id: str,
    actor: str,
    reason: str,
    status: str,
    reason_code: str | None,
) -> None:
    log_audit(
        "sfu_node_identity_mutation",
        {
            "operation": operation,
            "node_id": node_id,
            "actor": actor,
            "reason": reason,
            "status": status,
            "reason_code": reason_code,
        },
    )


def _error(exc: SfuNodeIdentityError):
    return api_response(
        status="error",
        message=exc.reason_code,
        data={"reason_code": exc.reason_code},
        code=exc.status_code,
    )


__all__ = ["webrtc_sfu_node_enrollment_bp"]
