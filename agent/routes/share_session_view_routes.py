"""Bounded view payload push/poll routes of share sessions."""

from __future__ import annotations

import time
import uuid
from typing import Any

from flask import Blueprint, jsonify, request

from agent.auth import check_user_auth
from agent.services.share_audit_service import (
    audit_view_delta_sent,
    audit_view_started,
)
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
    _STRICT_VIEW_PERMISSIONS,
    _STRICT_VIEW_TRAFFIC,
    _view_started_audited,
)


_VIEW_QUEUE_MAX = 50
_VIEW_PAYLOAD_MAX_BYTES = 256 * 1024
_VIEW_FRAME_RATE = {"namespace": "share_view_push", "limit": 40, "window_seconds": 10}
_VIEW_POLL_RATE = {"namespace": "share_view_poll", "limit": 80, "window_seconds": 10}


@check_user_auth
def push_view_payload(session_id: str):
    """Relay an opaque Pair payload; strict sessions expose no side metadata."""
    user_id = _current_user_id()
    service = get_share_session_service()
    session_item = service.get_session(session_id)
    if not isinstance(session_item, dict):
        return jsonify({"error": "session_not_found"}), 404
    if not _is_session_active(session_item):
        return jsonify({"error": "session_not_active"}), 403
    if not _is_active_participant(session_id=session_id, user_id=user_id, session_item=session_item):
        return jsonify({"error": "not_a_participant"}), 403
    session_owner_user_id = str(session_item.get("owner_user_id") or "")
    if not _rate_limiter.allow_request(
        namespace=_VIEW_FRAME_RATE["namespace"],
        subject=f"{user_id}:{session_id}",
        limit=_VIEW_FRAME_RATE["limit"],
        window_seconds=_VIEW_FRAME_RATE["window_seconds"],
    ):
        return jsonify({"error": "rate_limited"}), 429
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    if not body.get("encrypted_payload"):
        return jsonify({"error": "encrypted_payload_required"}), 400
    raw = request.get_data(as_text=False)
    if len(raw) > _VIEW_PAYLOAD_MAX_BYTES:
        return jsonify({"error": "payload_too_large"}), 413
    if _strict_e2ee_enabled(session_item):
        if set(body) != {"message_id", "encrypted_payload"}:
            return jsonify({"error": "strict_envelope_fields_invalid"}), 400
        message_id = str(body.get("message_id") or "")
        if not message_id or len(message_id.encode("utf-8")) > 96:
            return jsonify({"error": "relay_item_id_invalid"}), 400
        current_epoch = get_webrtc_epoch_service().current_epoch("session", session_id)
        if current_epoch is None:
            return jsonify({"error": "security_epoch_unavailable"}), 409
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
                allowed_payload_types=_STRICT_VIEW_TRAFFIC,
                traffic_by_payload=_STRICT_VIEW_TRAFFIC,
                expected_contract_digest=str(contract["digest"]),
                authorizer=_strict_pair_authorizer(
                    session_id=session_id,
                    session_item=session_item,
                    permission_by_payload=_STRICT_VIEW_PERMISSIONS,
                    contract_digest=str(contract["digest"]),
                ),
            )
        except (ShareViewSecurityError, ShareSecurityNegotiationError) as exc:
            return jsonify({"error": exc.reason_code}), exc.status_code
        try:
            get_share_relay_compatibility_service().publish_secure_envelope(
                tenant_id=str(session_item.get("tenant_id") or "default"),
                session_id=session_id,
                epoch=secure.epoch,
                sender_id=user_id,
                audience_id=secure.recipient.id,
                traffic_class="visual_semantic",
                item_id=message_id,
                item_id_field="message_id",
                serialized_envelope=str(body["encrypted_payload"]),
                queue_limit=_VIEW_QUEUE_MAX,
            )
        except ShareRelayCompatibilityError as exc:
            status = 413 if exc.reason_code in {"relay_envelope_too_large", "relay_item_invalid"} else 429
            return jsonify({"error": exc.reason_code}), status
        if secure.payload_type == "pair.view_delta" and session_id not in _view_started_audited:
            audit_view_started(session_id=session_id, owner_user_id=session_owner_user_id)
            _view_started_audited.add(session_id)
        audit_view_delta_sent(
            session_id=session_id,
            owner_user_id=session_owner_user_id,
            sender_user_id=user_id,
            kind=secure.payload_type,
            new_hash="",
            policy_hash=secure.aad.contract_digest,
        )
        return jsonify({"ok": True}), 200

    if session_item.get("owner_user_id") != user_id:
        return jsonify({"error": "forbidden"}), 403
    if not get_share_session_permission_service().allows(session_id, session_item.get("permissions"), "view_tui"):
        return jsonify({"error": "view_tui_permission_required"}), 403
    if session_id not in _view_started_audited:
        audit_view_started(session_id=session_id, owner_user_id=user_id)
        _view_started_audited.add(session_id)
    message_id = str(body.get("message_id") or str(uuid.uuid4()))
    entry: dict[str, Any] = {
        "session_id": session_id,
        "message_id": message_id,
        "kind": str(body.get("kind") or "snapshot"),
        "width": int(body.get("width") or 0),
        "height": int(body.get("height") or 0),
        "base_hash": str(body.get("base_hash") or ""),
        "new_hash": str(body.get("new_hash") or ""),
        "encrypted_payload": body.get("encrypted_payload"),
        "pushed_at": time.time(),
    }
    audience_ids = [
        str(participant.get("user_id") or "")
        for participant in service.get_participants(session_id)
        if participant.get("revoked_at") is None
    ]
    epoch = get_webrtc_epoch_service().current_epoch("session", session_id) or 1
    try:
        get_share_relay_compatibility_service().publish(
            tenant_id=str(session_item.get("tenant_id") or "default"),
            session_id=session_id,
            epoch=epoch,
            sender_id=user_id,
            audience_ids=audience_ids,
            traffic_class="visual_semantic",
            item=entry,
            item_id_field="message_id",
            queue_limit=_VIEW_QUEUE_MAX,
        )
    except ShareRelayCompatibilityError as exc:
        status = 413 if exc.reason_code in {"relay_envelope_too_large", "relay_item_invalid"} else 429
        return jsonify({"error": exc.reason_code}), status
    audit_view_delta_sent(
        session_id=session_id,
        owner_user_id=session_owner_user_id,
        sender_user_id=user_id,
        kind=str(entry["kind"]),
        new_hash=str(entry["new_hash"]),
        policy_hash=str(entry["base_hash"] or entry["new_hash"] or ""),
    )
    return jsonify({"ok": True}), 200


@check_user_auth
def poll_view_payload(session_id: str):
    """SS05.04: Teilnehmer holt verschlüsselte Snapshots/Deltas ab."""
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"error": "not_authenticated"}), 401
    service = get_share_session_service()
    session_item = service.get_session(session_id)
    if not isinstance(session_item, dict):
        return jsonify({"error": "session_not_found"}), 404
    if not _is_session_active(session_item):
        return jsonify({"error": "session_not_active"}), 403
    if not _rate_limiter.allow_request(
        namespace=_VIEW_POLL_RATE["namespace"],
        subject=f"{user_id}:{session_id}",
        limit=_VIEW_POLL_RATE["limit"],
        window_seconds=_VIEW_POLL_RATE["window_seconds"],
    ):
        return jsonify({"error": "rate_limited"}), 429
    if not _is_active_participant(session_id=session_id, user_id=user_id, session_item=session_item):
        return jsonify({"error": "not_a_participant"}), 403
    if not _strict_e2ee_enabled(session_item) and not get_share_session_permission_service().allows(
        session_id, session_item.get("permissions"), "view_tui"
    ):
        return jsonify({"error": "view_tui_permission_required"}), 403
    since = str(request.args.get("since") or "").strip()
    frames, last_id = get_share_relay_compatibility_service().read(
        tenant_id=str(session_item.get("tenant_id") or "default"),
        session_id=session_id,
        audience_id=user_id,
        traffic_class="visual_semantic",
        since_item_id=since,
        item_id_field="message_id",
        queue_limit=_VIEW_QUEUE_MAX,
        page_limit=10,
    )
    if _strict_e2ee_enabled(session_item):
        view_messages = [
            {"message_id": frame.get("message_id"), "encrypted_payload": frame.get("encrypted_payload")}
            for frame in frames
        ]
        return jsonify({"ok": True, "view_messages": view_messages, "view_cursor": last_id or ""}), 200

    # T06: Pair-Dev view-sync contract. The frontend expects
    # `view_messages` (a flat list of RelayEnvelopes) and a
    # `view_cursor` to advance through the queue. We keep the
    # legacy `data.frames` shape for backwards compatibility
    # with any older client still on it.
    view_messages = [
        {
            "message_id": f.get("message_id"),
            "kind": f.get("kind"),
            "base_hash": f.get("base_hash"),
            "new_hash": f.get("new_hash"),
            "width": f.get("width"),
            "height": f.get("height"),
            "encrypted_payload": f.get("encrypted_payload"),
        }
        for f in frames
    ]
    return jsonify(
        {
            "ok": True,
            "view_messages": view_messages,
            "messages": view_messages,
            "payloads": view_messages,
            "view_cursor": last_id or "",
            "data": {"frames": frames},
        }
    ), 200


def register_share_session_view_routes(blueprint: Blueprint) -> None:
    """Register these handlers on the share-session blueprint in their original order."""

    blueprint.add_url_rule(
        "/share-sessions/<session_id>/view/push",
        view_func=push_view_payload,
        methods=["POST"],
    )
    blueprint.add_url_rule(
        "/share-sessions/<session_id>/view/poll",
        view_func=poll_view_payload,
        methods=["GET"],
    )


__all__ = [
    "push_view_payload",
    "poll_view_payload",
    "register_share_session_view_routes",
]
