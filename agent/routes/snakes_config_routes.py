"""Snake config CRUD endpoints — registration, listing, heartbeat, legacy messages."""

from __future__ import annotations

import secrets
import time
from typing import Any

from flask import jsonify, request

from .snakes import (
    _MAX_SNAKES,
    _VALID_COLORS,
    _VALID_ROLES,
    _is_local_request,
    _messages,
    _next_free_color,
    _optional_user_auth,
    _request_device_id,
    _snakes,
    _snake_bound_to_auth,
    snakes_bp,
)


@snakes_bp.route("/snakes", methods=["POST"])
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
    auth = _optional_user_auth()
    if not auth and not _is_local_request():
        return jsonify({"error": "oidc_login_required_or_local_dev_only"}), 401
    oidc_id = str(auth.get("sub") or auth.get("username") or "")
    owner_device_id = _request_device_id()

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
        "auth_mode": "user_jwt" if auth else "legacy_local_dev",
        "token": token,
        "active": True,
        "registered_at": time.time(),
        "last_heartbeat": time.time(),
    }
    _messages[snake_id] = []
    return jsonify({"id": snake_id, "token": token, "color": color}), 201


@snakes_bp.route("/snakes", methods=["GET"])
def list_snakes():
    """GET /snakes -- alle aktiven Snakes auflisten."""
    result = []
    for snake in _snakes.values():
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
def deregister_snake(snake_id: str):
    """DELETE /snakes/<id> -- Snake abmelden."""
    snake = _snakes.get(snake_id)
    if not snake:
        return jsonify({"error": "Snake nicht gefunden"}), 404
    snake["active"] = False
    return jsonify({"ok": True, "id": snake_id}), 200


@snakes_bp.route("/snakes/<snake_id>/heartbeat", methods=["POST"])
def snake_heartbeat(snake_id: str):
    """POST /snakes/<id>/heartbeat -- Liveness-Ping."""
    snake = _snakes.get(snake_id)
    if not snake or not snake.get("active"):
        return jsonify({"error": "Snake nicht gefunden oder inaktiv"}), 404
    snake["last_heartbeat"] = time.time()
    return jsonify({"ok": True}), 200


@snakes_bp.route("/snakes/<snake_id>/messages", methods=["POST"])
def send_message(snake_id: str):
    """POST /snakes/<id>/messages -- Nachricht an Snake senden. Body: {from_id, text, priority?}"""
    snake = _snakes.get(snake_id)
    if not snake or not snake.get("active"):
        return jsonify({"error": "Ziel-Snake nicht gefunden oder inaktiv"}), 404
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    from_id = str(body.get("from_id") or "unknown")
    text = str(body.get("text") or "").strip()[:200]
    if not text:
        return jsonify({"error": "text erforderlich"}), 400
    priority = int(body.get("priority") or 5)
    msg: dict[str, Any] = {
        "from_id": from_id,
        "text": text,
        "priority": priority,
        "at": time.time(),
    }
    inbox = _messages.setdefault(snake_id, [])
    inbox.append(msg)
    if len(inbox) > 20:
        inbox[:] = inbox[-20:]
    return jsonify({"ok": True}), 202


@snakes_bp.route("/snakes/<snake_id>/messages", methods=["GET"])
def get_messages(snake_id: str):
    """GET /snakes/<id>/messages -- Nachrichten abrufen (legacy)."""
    snake = _snakes.get(snake_id)
    if not snake:
        return jsonify({"error": "Snake nicht gefunden"}), 404
    msgs = _messages.get(snake_id, [])
    _messages[snake_id] = []
    return jsonify({"messages": msgs}), 200


@snakes_bp.route("/snakes/participants", methods=["GET"])
def list_participants():
    """GET /snakes/participants -- Aktive Teilnehmer mit Rolle, Farbe, Status."""
    now = time.time()
    result = []
    for snake in _snakes.values():
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
