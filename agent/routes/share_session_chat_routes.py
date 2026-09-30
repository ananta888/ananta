"""Bounded chat message routes of share sessions."""

from __future__ import annotations

import time
import uuid
from typing import Any

from flask import Blueprint, jsonify, request

from agent.auth import check_user_auth
from agent.services.share_audit_service import audit_chat_sent
from agent.services.share_relay_compatibility_service import (
    ShareRelayCompatibilityError,
    get_share_relay_compatibility_service,
)
from agent.services.share_security_negotiation_service import (
    ShareSecurityNegotiationError,
)
from agent.services.share_session_permissions import (
    get_share_session_permission_service,
)
from agent.services.share_session_service import get_share_session_service
from agent.services.share_view_security_service import ShareViewSecurityError
from agent.services.webrtc_epoch_service import get_webrtc_epoch_service
from agent.routes.share_session_route_support import (
    _current_user_id,
    _is_active_participant,
    _is_session_active,
    _rate_limiter,
    _share_envelope_security,
    _strict_e2ee_enabled,
    _strict_pair_authorizer,
    _strict_security_contract,
)


_CHAT_QUEUE_MAX = 200
_CHAT_MSG_MAX_BYTES = 64 * 1024


_CHAT_SEND_RATE = {"namespace": "share_chat_send", "limit": 60, "window_seconds": 10}
_CHAT_POLL_RATE = {"namespace": "share_chat_poll", "limit": 120, "window_seconds": 10}


@check_user_auth
def send_share_chat_message(session_id: str):
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"error": "not_authenticated"}), 401
    service = get_share_session_service()
    session_item = service.get_session(session_id)
    if not isinstance(session_item, dict):
        return jsonify({"error": "session_not_found"}), 404
    if not _is_session_active(session_item):
        return jsonify({"error": "session_not_active", "blocked": True}), 403
    if not _is_active_participant(session_id=session_id, user_id=user_id, session_item=session_item):
        return jsonify({"error": "not_a_participant", "blocked": True}), 403
    if not get_share_session_permission_service().allows(session_id, session_item.get("permissions"), "chat"):
        return jsonify({"error": "chat_permission_required", "blocked": True}), 403
    if not _rate_limiter.allow_request(
        namespace=_CHAT_SEND_RATE["namespace"],
        subject=f"{user_id}:{session_id}",
        limit=_CHAT_SEND_RATE["limit"],
        window_seconds=_CHAT_SEND_RATE["window_seconds"],
    ):
        return jsonify({"error": "rate_limited", "blocked": True}), 429

    raw = request.get_data(as_text=False)
    if len(raw) > _CHAT_MSG_MAX_BYTES:
        return jsonify({"error": "payload_too_large", "blocked": True}), 413
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    if _strict_e2ee_enabled(session_item):
        if set(body) != {"id", "encrypted_payload"}:
            return jsonify({"error": "strict_envelope_fields_invalid", "blocked": True}), 400
        message_id = str(body.get("id") or "")
        if not message_id or len(message_id.encode("utf-8")) > 96:
            return jsonify({"error": "relay_item_id_invalid", "blocked": True}), 400
        current_epoch = get_webrtc_epoch_service().current_epoch("session", session_id)
        if current_epoch is None:
            return jsonify({"error": "security_epoch_unavailable", "blocked": True}), 409
        try:
            contract = _strict_security_contract(
                session_id=session_id,
                session_item=session_item,
                epoch=current_epoch,
            )
            secure = _share_envelope_security.validate(
                session_id=session_id,
                authenticated_sender_id=user_id,
                serialized=body.get("encrypted_payload"),
                allowed_payload_types={"pair.chat_message"},
                traffic_by_payload={"pair.chat_message": "semantic"},
                expected_contract_digest=str(contract["digest"]),
                authorizer=_strict_pair_authorizer(
                    session_id=session_id,
                    session_item=session_item,
                    permission_by_payload={"pair.chat_message": "chat"},
                    contract_digest=str(contract["digest"]),
                ),
            )
            get_share_relay_compatibility_service().publish_secure_envelope(
                tenant_id=str(session_item.get("tenant_id") or "default"),
                session_id=session_id,
                epoch=secure.epoch,
                sender_id=user_id,
                audience_id=secure.recipient.id,
                traffic_class="transcript",
                item_id=message_id,
                item_id_field="id",
                serialized_envelope=str(body["encrypted_payload"]),
                queue_limit=_CHAT_QUEUE_MAX,
            )
        except (ShareViewSecurityError, ShareSecurityNegotiationError) as exc:
            return jsonify({"error": exc.reason_code, "blocked": True}), exc.status_code
        except ShareRelayCompatibilityError as exc:
            status = 413 if exc.reason_code in {"relay_envelope_too_large", "relay_item_invalid"} else 429
            return jsonify({"error": exc.reason_code, "blocked": True}), status
        audit_chat_sent(
            session_id=session_id,
            sender_user_id=user_id,
            message_id=message_id,
            is_encrypted=True,
        )
        return jsonify({"ok": True, "data": {"id": message_id}, "blocked": False}), 201

    message_id = str(body.get("id") or str(uuid.uuid4()))
    encrypted_payload = body.get("encrypted_payload")
    text = str(body.get("text") or "")
    if not encrypted_payload and not text:
        return jsonify({"error": "message_required", "blocked": True}), 400
    message = {
        "id": message_id,
        "share_session_id": session_id,
        "from_id": str(body.get("from_id") or user_id),
        "channel_type": str(body.get("channel_type") or "room"),
        "visibility": str(body.get("visibility") or "room"),
        "encrypted_payload": encrypted_payload,
        "text": text,
        "created_at": time.time(),
    }
    audience_ids = {
        str(session_item.get("owner_user_id") or ""),
        user_id,
        *(
            str(participant.get("user_id") or "")
            for participant in service.get_participants(session_id)
            if participant.get("revoked_at") is None
        ),
    }
    epoch = get_webrtc_epoch_service().current_epoch("session", session_id) or 1
    try:
        get_share_relay_compatibility_service().publish(
            tenant_id=str(session_item.get("tenant_id") or "default"),
            session_id=session_id,
            epoch=epoch,
            sender_id=user_id,
            audience_ids=list(audience_ids),
            traffic_class="transcript",
            item=message,
            item_id_field="id",
            queue_limit=_CHAT_QUEUE_MAX,
        )
    except ShareRelayCompatibilityError as exc:
        status = 413 if exc.reason_code in {"relay_envelope_too_large", "relay_item_invalid"} else 429
        return jsonify({"error": exc.reason_code, "blocked": True}), status
    audit_chat_sent(
        session_id=session_id,
        sender_user_id=user_id,
        message_id=message_id,
        is_encrypted=bool(encrypted_payload),
    )
    return jsonify({"ok": True, "data": {"id": message_id}, "blocked": False}), 201


@check_user_auth
def list_share_chat_messages(session_id: str):
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"error": "not_authenticated"}), 401
    service = get_share_session_service()
    session_item = service.get_session(session_id)
    if not isinstance(session_item, dict):
        return jsonify({"error": "session_not_found"}), 404
    if not _is_session_active(session_item):
        return jsonify({"error": "session_not_active"}), 403
    if not _is_active_participant(session_id=session_id, user_id=user_id, session_item=session_item):
        return jsonify({"error": "not_a_participant"}), 403
    if not get_share_session_permission_service().allows(session_id, session_item.get("permissions"), "chat"):
        return jsonify({"error": "chat_permission_required"}), 403
    if not _rate_limiter.allow_request(
        namespace=_CHAT_POLL_RATE["namespace"],
        subject=f"{user_id}:{session_id}",
        limit=_CHAT_POLL_RATE["limit"],
        window_seconds=_CHAT_POLL_RATE["window_seconds"],
    ):
        return jsonify({"error": "rate_limited"}), 429

    since = str(request.args.get("since") or "").strip()
    messages, cursor = get_share_relay_compatibility_service().read(
        tenant_id=str(session_item.get("tenant_id") or "default"),
        session_id=session_id,
        audience_id=user_id,
        traffic_class="transcript",
        since_item_id=since,
        item_id_field="id",
        queue_limit=_CHAT_QUEUE_MAX,
        page_limit=100,
    )
    return jsonify({"ok": True, "messages": messages, "cursor": cursor}), 200


def register_share_session_chat_routes(blueprint: Blueprint) -> None:
    """Register these handlers on the share-session blueprint in their original order."""

    blueprint.add_url_rule(
        "/share-sessions/<session_id>/chat/messages",
        view_func=send_share_chat_message,
        methods=["POST"],
    )
    blueprint.add_url_rule(
        "/share-sessions/<session_id>/chat/messages",
        view_func=list_share_chat_messages,
        methods=["GET"],
    )


__all__ = [
    "send_share_chat_message",
    "list_share_chat_messages",
    "register_share_session_chat_routes",
]
