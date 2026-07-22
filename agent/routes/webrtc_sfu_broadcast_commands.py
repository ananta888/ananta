"""Authenticated command boundary delegating all SFU mutations to the Hub."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from flask import Blueprint, current_app, jsonify, request

from agent.auth import check_user_auth, get_request_auth_context
from agent.common.audit import log_audit
from agent.services.sfu_broadcast_command_service import (
    SfuBroadcastCommand,
    SfuBroadcastCommandError,
    SfuBroadcastCommandPrincipal,
    SfuBroadcastCommandService,
)


webrtc_sfu_broadcast_commands_bp = Blueprint("webrtc_sfu_broadcast_commands", __name__)
_LEGACY_BODY_KEYS = frozenset(
    {"room_ref", "command", "expected_version", "confirmed", "options"}
)
_V1_BODY_KEYS = _LEGACY_BODY_KEYS | {"schema", "reason"}
_MAX_BODY_BYTES = 4096


@webrtc_sfu_broadcast_commands_bp.post("/v1/semantic-media/sfu/broadcast/commands")
@check_user_auth
def apply_broadcast_command():
    try:
        if request.content_length is not None and request.content_length > _MAX_BODY_BYTES:
            raise SfuBroadcastCommandError("sfu_command_payload_too_large", 413)
        body = request.get_json(silent=True)
        if not isinstance(body, Mapping) or set(body) not in {
            _LEGACY_BODY_KEYS,
            _V1_BODY_KEYS,
        }:
            raise SfuBroadcastCommandError("sfu_command_payload_invalid")
        command = SfuBroadcastCommand(
            room_ref=body.get("room_ref"),
            action=body.get("command"),
            expected_version=body.get("expected_version"),
            confirmed=body.get("confirmed"),
            options=body.get("options"),
            schema=body.get(
                "schema", "ananta.webrtc.sfu-broadcast-user-intent.v1"
            ),
            reason=body.get("reason", "user_requested"),
        )
        result = _service().execute(
            _principal(),
            command,
            idempotency_key=str(request.headers.get("Idempotency-Key") or ""),
        )
    except SfuBroadcastCommandError as exc:
        _audit("denied", exc.reason_code)
        return jsonify({"ok": False, "accepted": False, "reason_code": exc.reason_code}), exc.status_code
    _audit("confirmed" if result.accepted else "denied", result.reason_code)
    return jsonify(result.public()), 200 if result.accepted else 409


def _principal() -> SfuBroadcastCommandPrincipal:
    auth = dict(get_request_auth_context() or {})
    subject = str(auth.get("sub") or auth.get("username") or "").strip()
    tenant = str(auth.get("tenant_id") or auth.get("tenant") or "").strip()
    role = str(auth.get("role") or "user").strip().casefold()
    return SfuBroadcastCommandPrincipal(subject, tenant, role, _scopes(auth.get("room_scopes")))


def _scopes(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return tuple(item for item in value if isinstance(item, str) and item)
    return ()


def _service() -> SfuBroadcastCommandService:
    service = current_app.extensions.get("sfu_broadcast_command_service")
    if not isinstance(service, SfuBroadcastCommandService):
        raise SfuBroadcastCommandError("sfu_command_service_unavailable", 503)
    return service


def _audit(outcome: str, reason_code: str) -> None:
    log_audit("sfu_broadcast_command", {"outcome": outcome, "reason_code": reason_code})


__all__ = ["webrtc_sfu_broadcast_commands_bp"]
