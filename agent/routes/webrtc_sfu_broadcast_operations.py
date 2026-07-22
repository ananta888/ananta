"""Authenticated, read-only HTTP boundary for SFU broadcast diagnostics."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from flask import Blueprint, current_app, jsonify, request

from agent.auth import check_user_auth, get_request_auth_context
from agent.common.audit import log_audit
from agent.services.sfu_broadcast_operations_read_model import (
    SfuBroadcastOperationsError,
    SfuBroadcastOperationsPrincipal,
    SfuBroadcastOperationsQuery,
    SfuBroadcastOperationsReadModel,
)


webrtc_sfu_broadcast_operations_bp = Blueprint("webrtc_sfu_broadcast_operations", __name__)
_QUERY_KEYS = frozenset({"room_ref", "receiver_ref", "tenant_ref", "region", "page_size", "cursor"})


@webrtc_sfu_broadcast_operations_bp.get("/v1/semantic-media/sfu/broadcast/operations")
@check_user_auth
def read_broadcast_operations():
    try:
        if set(request.args) - _QUERY_KEYS:
            raise SfuBroadcastOperationsError("sfu_operations_query_field_not_allowed")
        principal = _principal()
        page = _service().query(principal, _query())
    except SfuBroadcastOperationsError as exc:
        _audit("denied", exc.reason_code)
        return jsonify({"ok": False, "reason_code": exc.reason_code}), exc.status_code
    _audit("read", page.reason_code)
    return jsonify(page.public()), 200


def _principal() -> SfuBroadcastOperationsPrincipal:
    auth = dict(get_request_auth_context() or {})
    subject = str(auth.get("sub") or auth.get("username") or "").strip()
    role = str(auth.get("role") or "user").strip().casefold()
    tenant = str(auth.get("tenant_id") or auth.get("tenant") or "").strip()
    tenants = _scopes(auth.get("tenant_scopes"))
    regions = _scopes(auth.get("region_scopes"))
    if role == "user" and tenant:
        tenants = (tenant,)
    return SfuBroadcastOperationsPrincipal(subject, role, tenants, regions)


def _query() -> SfuBroadcastOperationsQuery:
    raw_page_size = request.args.get("page_size", "25")
    try:
        page_size = int(raw_page_size)
    except (TypeError, ValueError) as exc:
        raise SfuBroadcastOperationsError("sfu_operations_page_size_invalid") from exc
    return SfuBroadcastOperationsQuery(
        room_ref=_optional("room_ref"),
        receiver_ref=_optional("receiver_ref"),
        tenant_ref=_optional("tenant_ref"),
        region=_optional("region"),
        page_size=page_size,
        cursor=_optional("cursor"),
    )


def _optional(name: str) -> str | None:
    value = request.args.get(name)
    return value if value not in (None, "") else None


def _scopes(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return tuple(item for item in value if isinstance(item, str) and item)
    return ()


def _service() -> SfuBroadcastOperationsReadModel:
    service = current_app.extensions.get("sfu_broadcast_operations_read_model")
    if not isinstance(service, SfuBroadcastOperationsReadModel):
        raise SfuBroadcastOperationsError("sfu_operations_service_unavailable", 503)
    return service


def _audit(outcome: str, reason_code: str) -> None:
    log_audit("sfu_broadcast_operations_read", {"outcome": outcome, "reason_code": reason_code})


__all__ = ["webrtc_sfu_broadcast_operations_bp"]
