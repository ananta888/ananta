"""Semantic relay envelope routes (push, poll, acknowledge) of share sessions."""

from __future__ import annotations

from typing import Any

from flask import Blueprint, jsonify, request

from agent.auth import check_user_auth
from agent.models.semantic_relay_errors import SemanticRelayRepositoryError
from agent.services.semantic_relay_authorization import (
    SemanticRelayAuthorizationError,
)
from agent.services.semantic_relay_composition import (
    get_semantic_relay_service,
)
from agent.services.semantic_relay_service import SemanticRelayServiceError
from agent.services.share_session_service import get_share_session_service
from agent.services.webrtc_epoch_service import get_webrtc_epoch_service
from ananta_contracts.webrtc_datachannel import DataChannelContractError
from agent.routes.share_session_route_support import (
    _current_tenant_id,
    _current_user_id,
    _is_active_participant,
    _is_session_active,
    _strict_e2ee_enabled,
)


def _semantic_relay_error(exc: Exception):
    if isinstance(exc, DataChannelContractError):
        return jsonify({"error": exc.reason_code, "field": exc.field or None}), exc.status_code
    if isinstance(exc, SemanticRelayServiceError):
        return jsonify({"error": exc.reason_code}), exc.status_code
    if isinstance(exc, SemanticRelayAuthorizationError):
        status = 409 if exc.reason_code == "relay_epoch_stale" else 403
        return jsonify({"error": exc.reason_code}), status
    if isinstance(exc, SemanticRelayRepositoryError):
        status = 409 if exc.reason_code == "relay_message_id_conflict" else 429
        return jsonify({"error": exc.reason_code}), status
    raise exc


@check_user_auth
def push_semantic_relay_envelope(session_id: str):
    """Persist one opaque, bilateral, epoch-bound DataChannel envelope."""

    user_id = _current_user_id()
    tenant_id = _current_tenant_id()
    service = get_share_session_service()
    session_item = service.get_session(session_id)
    if not isinstance(session_item, dict):
        return jsonify({"error": "session_not_found"}), 404
    if not _is_session_active(session_item):
        return jsonify({"error": "session_not_active"}), 403
    if not _is_active_participant(session_id=session_id, user_id=user_id, session_item=session_item):
        return jsonify({"error": "not_a_participant"}), 403
    if not _strict_e2ee_enabled(session_item):
        return jsonify({"error": "strict_e2ee_required"}), 409
    raw = request.get_data(cache=True, as_text=False)
    try:
        stored = get_semantic_relay_service().append_wire(
            tenant_id=tenant_id,
            authenticated_sender_id=user_id,
            expected_session_id=session_id,
            raw=raw,
        )
    except (
        DataChannelContractError,
        SemanticRelayAuthorizationError,
        SemanticRelayRepositoryError,
        SemanticRelayServiceError,
    ) as exc:
        return _semantic_relay_error(exc)
    return jsonify(
        {
            "ok": True,
            "message_id": stored["message_id"],
            "cursor": stored["cursor"],
            "traffic_class": stored["traffic_class"],
        }
    ), 201


@check_user_auth
def poll_semantic_relay_envelopes(session_id: str):
    user_id = _current_user_id()
    tenant_id = _current_tenant_id()
    share_service = get_share_session_service()
    session_item = share_service.get_session(session_id)
    if not isinstance(session_item, dict):
        return jsonify({"error": "session_not_found"}), 404
    if not _is_session_active(session_item):
        return jsonify({"error": "session_not_active"}), 403
    if not _is_active_participant(session_id=session_id, user_id=user_id, session_item=session_item):
        return jsonify({"error": "not_a_participant"}), 403
    if not _strict_e2ee_enabled(session_item):
        return jsonify({"error": "strict_e2ee_required"}), 409
    traffic_class = str(request.args.get("traffic_class") or "")
    current_epoch = get_webrtc_epoch_service().current_epoch("session", session_id)
    try:
        epoch = int(request.args.get("epoch") or current_epoch or 0)
        cursor = int(request.args.get("cursor") or 0)
        limit = int(request.args.get("limit") or 50)
    except (TypeError, ValueError):
        return jsonify({"error": "relay_query_invalid"}), 400
    if current_epoch is None or epoch != current_epoch:
        return jsonify({"error": "relay_epoch_stale"}), 409
    try:
        page = get_semantic_relay_service().read_after(
            tenant_id=tenant_id,
            audience_id=user_id,
            session_id=session_id,
            epoch=epoch,
            traffic_class=traffic_class,
            cursor=cursor,
            limit=limit,
        )
    except (SemanticRelayAuthorizationError, SemanticRelayServiceError) as exc:
        return _semantic_relay_error(exc)
    return jsonify({"ok": True, **page}), 200


@check_user_auth
def acknowledge_semantic_relay_envelopes(session_id: str):
    user_id = _current_user_id()
    tenant_id = _current_tenant_id()
    share_service = get_share_session_service()
    session_item = share_service.get_session(session_id)
    if not isinstance(session_item, dict):
        return jsonify({"error": "session_not_found"}), 404
    if not _is_active_participant(session_id=session_id, user_id=user_id, session_item=session_item):
        return jsonify({"error": "not_a_participant"}), 403
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    traffic_class = str(body.get("traffic_class") or "")
    current_epoch = get_webrtc_epoch_service().current_epoch("session", session_id)
    epoch = body.get("epoch")
    cursor = body.get("cursor")
    if (
        not isinstance(epoch, int)
        or isinstance(epoch, bool)
        or epoch != current_epoch
        or not isinstance(cursor, int)
        or isinstance(cursor, bool)
        or cursor < 0
    ):
        return jsonify({"error": "relay_ack_invalid"}), 400
    try:
        acknowledged = get_semantic_relay_service().acknowledge(
            tenant_id=tenant_id,
            audience_id=user_id,
            session_id=session_id,
            epoch=epoch,
            traffic_class=traffic_class,
            cursor=cursor,
        )
    except (SemanticRelayAuthorizationError, SemanticRelayServiceError) as exc:
        return _semantic_relay_error(exc)
    return jsonify({"ok": True, "acknowledged_cursor": acknowledged}), 200


def register_share_session_relay_routes(blueprint: Blueprint) -> None:
    """Register these handlers on the share-session blueprint in their original order."""

    blueprint.add_url_rule(
        "/share-sessions/<session_id>/semantic-relay",
        view_func=push_semantic_relay_envelope,
        methods=["POST"],
    )
    blueprint.add_url_rule(
        "/share-sessions/<session_id>/semantic-relay",
        view_func=poll_semantic_relay_envelopes,
        methods=["GET"],
    )
    blueprint.add_url_rule(
        "/share-sessions/<session_id>/semantic-relay/ack",
        view_func=acknowledge_semantic_relay_envelopes,
        methods=["POST"],
    )


__all__ = [
    "push_semantic_relay_envelope",
    "poll_semantic_relay_envelopes",
    "acknowledge_semantic_relay_envelopes",
    "register_share_session_relay_routes",
]
