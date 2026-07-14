"""Snake config CRUD endpoints — registration, listing, heartbeat, legacy messages."""

from __future__ import annotations

import secrets
import time
from typing import Any

from flask import jsonify, request

from .snake_event_broadcaster import drop_snake_queue
from .snakes_state import (
    _MAX_SNAKES,
    _VALID_COLORS,
    _VALID_ROLES,
    _authenticated_snake_control_auth,
    _chat_principal_from_auth,
    _check_snake_control_auth,
    _messages,
    _next_free_color,
    _request_device_id,
    _snake_bound_to_auth,
    _snake_owner_principal,
    _snakes,
    snakes_bp,
)


def _authenticated_owned_snake(snake_id: str):
    """Return one exactly user/tenant-bound Snake without existence oracles."""

    auth = _authenticated_snake_control_auth()
    if not auth:
        return None, None, (
            jsonify({"error": "user_authentication_required"}),
            401,
        )
    snake = _snakes.get(snake_id)
    if snake is None or not _snake_bound_to_auth(snake, auth):
        return None, auth, (
            jsonify({"error": "snake_not_found", "error_code": "snake_not_found"}),
            404,
        )
    return snake, auth, None


@snakes_bp.route("/snakes", methods=["POST"])
@_check_snake_control_auth
def register_snake():
    """POST /snakes -- Snake registrieren. Body: {name, role, color?, oidc_id?}"""
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    name = str(body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name erforderlich"}), 400
    role = str(body.get("role") or "viewer")
    if role not in _VALID_ROLES:
        role = "viewer"
    color = str(body.get("color") or "")
    if color not in _VALID_COLORS:
        color = _next_free_color()
    # check color collision
    used_colors = {s["color"] for s in _snakes.values() if s.get("active")}
    if color in used_colors:
        color = _next_free_color()
    auth = _authenticated_snake_control_auth()
    if not auth:
        return jsonify({"error": "authentication_required"}), 401
    oidc_id = str(auth.get("sub") or auth.get("username") or "")
    owner_device_id = _request_device_id()
    owner_principal = _chat_principal_from_auth(auth)
    if owner_principal is None:
        return jsonify({"error": "canonical_identity_required"}), 401

    active_count = sum(1 for s in _snakes.values() if s.get("active"))
    if active_count >= _MAX_SNAKES:
        return jsonify({"error": f"Maximale Snake-Anzahl ({_MAX_SNAKES}) erreicht"}), 409

    snake_id = f"s-{secrets.token_hex(4)}"
    token = secrets.token_urlsafe(32)
    _snakes[snake_id] = {
        "id": snake_id,
        "name": name,
        "role": role,
        "color": color,
        "oidc_id": oidc_id,
        "owner_device_id": owner_device_id,
        "owner_principal": owner_principal.to_dict(),
        "auth_mode": str(auth.get("auth_mode") or "user_jwt"),
        "token": token,
        "active": True,
        "registered_at": time.time(),
        "last_heartbeat": time.time(),
    }
    _messages[snake_id] = []
    return jsonify({"id": snake_id, "token": token, "color": color}), 201


@snakes_bp.route("/snakes", methods=["GET"])
@_check_snake_control_auth
def list_snakes():
    """GET /snakes -- list only Snakes owned by the exact user principal."""
    auth = _authenticated_snake_control_auth()
    principal = _chat_principal_from_auth(auth) if auth else None
    if principal is None:
        return jsonify({"error": "user_authentication_required"}), 401
    result = []
    for snake in _snakes.values():
        if _snake_owner_principal(snake) != principal:
            continue
        age = time.time() - float(snake.get("last_heartbeat", 0))
        result.append({
            "id": snake["id"],
            "name": snake["name"],
            "role": snake["role"],
            "color": snake["color"],
            "oidc_id": snake.get("oidc_id") or "",
            "active": bool(snake.get("active")),
            "status": "online" if age < 30 else "offline",
            "last_heartbeat": snake.get("last_heartbeat"),
        })
    return jsonify({"snakes": result}), 200


@snakes_bp.route("/snakes/<snake_id>", methods=["DELETE"])
@_check_snake_control_auth
def deregister_snake(snake_id: str):
    """DELETE /snakes/<id> -- Snake abmelden."""
    snake, _auth, error = _authenticated_owned_snake(snake_id)
    if error is not None:
        return error
    assert snake is not None
    snake["active"] = False
    drop_snake_queue(snake_id)
    return jsonify({"ok": True, "id": snake_id}), 200


@snakes_bp.route("/snakes/<snake_id>/heartbeat", methods=["POST"])
@_check_snake_control_auth
def snake_heartbeat(snake_id: str):
    """POST /snakes/<id>/heartbeat -- Liveness-Ping."""
    snake, _auth, error = _authenticated_owned_snake(snake_id)
    if error is not None:
        return error
    assert snake is not None
    if not snake.get("active"):
        return jsonify({"error": "snake_not_found", "error_code": "snake_not_found"}), 404
    snake["last_heartbeat"] = time.time()
    return jsonify({"ok": True}), 200


@snakes_bp.route("/snakes/<snake_id>/messages", methods=["POST"])
@_check_snake_control_auth
def send_message(snake_id: str):
    """POST /snakes/<id>/messages -- Nachricht an Snake senden. Body: {from_id, text, priority?}"""
    snake, auth, error = _authenticated_owned_snake(snake_id)
    if error is not None:
        return error
    assert snake is not None and auth is not None
    if not snake.get("active"):
        return jsonify({"error": "snake_not_found", "error_code": "snake_not_found"}), 404
    principal = _chat_principal_from_auth(auth)
    if principal is None:
        return jsonify({"error": "canonical_identity_required"}), 401
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    from_id = str(body.get("from_id") or snake_id)
    sender = _snakes.get(from_id)
    if sender is None or _snake_owner_principal(sender) != principal:
        return jsonify({"error": "sender_snake_not_found", "error_code": "sender_snake_not_found"}), 404
    text = str(body.get("text") or "").strip()[:200]
    if not text:
        return jsonify({"error": "text erforderlich"}), 400
    priority = int(body.get("priority") or 5)
    msg: dict[str, Any] = {
        "from_id": from_id,
        "text": text,
        "priority": priority,
        "at": time.time(),
        "owner_principal": principal.to_dict(),
    }
    inbox = _messages.setdefault(snake_id, [])
    inbox.append(msg)
    if len(inbox) > 20:
        inbox[:] = inbox[-20:]
    return jsonify({"ok": True}), 202


@snakes_bp.route("/snakes/<snake_id>/messages", methods=["GET"])
@_check_snake_control_auth
def get_messages(snake_id: str):
    """GET /snakes/<id>/messages -- Nachrichten abrufen (legacy)."""
    snake, auth, error = _authenticated_owned_snake(snake_id)
    if error is not None:
        return error
    assert snake is not None and auth is not None
    principal = _chat_principal_from_auth(auth)
    if principal is None:
        return jsonify({"error": "canonical_identity_required"}), 401
    expected_owner = principal.to_dict()
    stored_messages = list(_messages.get(snake_id, []))
    msgs = [
        message
        for message in stored_messages
        if not isinstance(message.get("owner_principal"), dict)
        or message.get("owner_principal") == expected_owner
    ]
    _messages[snake_id] = [
        message
        for message in stored_messages
        if isinstance(message.get("owner_principal"), dict)
        and message.get("owner_principal") != expected_owner
    ]
    return jsonify(
        {
            "messages": [
                {key: value for key, value in message.items() if key != "owner_principal"}
                for message in msgs
            ]
        }
    ), 200


@snakes_bp.route("/snakes/participants", methods=["GET"])
@_check_snake_control_auth
def list_participants():
    """GET /snakes/participants -- Aktive Teilnehmer mit Rolle, Farbe, Status."""
    auth = _authenticated_snake_control_auth()
    principal = _chat_principal_from_auth(auth) if auth else None
    if principal is None:
        return jsonify({"error": "user_authentication_required"}), 401
    now = time.time()
    result = []
    for snake in _snakes.values():
        if _snake_owner_principal(snake) != principal:
            continue
        age = now - float(snake.get("last_heartbeat", 0))
        result.append({
            "id": snake["id"],
            "name": snake["name"],
            "role": snake["role"],
            "color": snake["color"],
            "status": "online" if age < 30 else "offline",
            "last_seen": snake.get("last_heartbeat"),
        })
    return jsonify({"participants": result}), 200
