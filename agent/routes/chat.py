from __future__ import annotations

import logging
from typing import Any

from flask import Blueprint, jsonify, request

from client_surfaces.operator_tui.chat_state import (
    get_sessions, get_session, add_session, update_session_settings,
    delete_session, make_session, set_active_session,
)
from client_surfaces.operator_tui.config.user_config_manager import get_manager

_log = logging.getLogger(__name__)

chat_bp = Blueprint("chat_api", __name__, url_prefix="/api/chat")


def _load_chat() -> dict[str, Any]:
    """Build a minimal chat dict from persisted user.json for session operations."""
    settings = get_manager().load()
    sessions = settings.get("chat_sessions") or []
    active_id = settings.get("chat_active_session_id") or (sessions[0]["id"] if sessions else "")
    return {"ai_sessions": sessions, "active_session_id": active_id, "channels": {}}


def _save_chat(chat: dict[str, Any]) -> None:
    """Persist sessions back to user.json."""
    get_manager().save({
        "chat_sessions": chat.get("ai_sessions") or [],
        "chat_active_session_id": chat.get("active_session_id") or "",
    })


@chat_bp.route("/sessions", methods=["GET"])
def list_chat_sessions():
    chat = _load_chat()
    sessions = get_sessions(chat)
    _save_chat(chat)  # persist any newly added default sessions / backfilled fields
    return jsonify([s.copy() for s in sessions])


@chat_bp.route("/sessions", methods=["POST"])
def create_chat_session():
    data = request.json
    if not data or not isinstance(data, dict):
        return jsonify({"error": "Invalid request body"}), 400

    session_id = data.get("id")
    name = data.get("name")
    if not session_id or not name:
        return jsonify({"error": "Session ID and name are required"}), 400

    chat = _load_chat()
    if get_session(chat, session_id):
        return jsonify({"error": f"Session with ID '{session_id}' already exists"}), 409

    new_session = make_session(
        session_id=session_id,
        name=name,
        system_prompt=data.get("system_prompt", ""),
        icon=data.get("icon", "💬"),
        group=data.get("group", ""),
        settings=data.get("settings") or {},
    )
    add_session(chat, new_session)
    set_active_session(chat, session_id)
    _save_chat(chat)
    return jsonify(new_session.copy()), 201


@chat_bp.route("/sessions/<session_id>", methods=["GET"])
def get_single_chat_session(session_id: str):
    chat = _load_chat()
    session = get_session(chat, session_id)
    if session is None:
        return jsonify({"error": f"Session '{session_id}' not found"}), 404
    return jsonify(session.copy())


@chat_bp.route("/sessions/<session_id>", methods=["PUT", "PATCH"])
def update_chat_session(session_id: str):
    chat = _load_chat()
    session = get_session(chat, session_id)
    if session is None:
        return jsonify({"error": f"Session '{session_id}' not found"}), 404

    data = request.json
    if not data or not isinstance(data, dict):
        return jsonify({"error": "Invalid request body"}), 400

    if "name" in data:
        session["name"] = data["name"]
    if "system_prompt" in data:
        session["system_prompt"] = data["system_prompt"]
    if "icon" in data:
        session["icon"] = data["icon"]
    if "group" in data:
        session["group"] = str(data["group"] or "")
    if "settings" in data and isinstance(data["settings"], dict):
        update_session_settings(chat, session_id, data["settings"])

    _save_chat(chat)
    session = get_session(chat, session_id)
    return jsonify((session or {}).copy())


@chat_bp.route("/sessions/<session_id>", methods=["DELETE"])
def delete_chat_session(session_id: str):
    chat = _load_chat()
    if get_session(chat, session_id) is None:
        return jsonify({"error": f"Session '{session_id}' not found"}), 404
    if len(get_sessions(chat)) <= 1:
        return jsonify({"error": "Cannot delete the last remaining session"}), 400
    delete_session(chat, session_id)
    _save_chat(chat)
    return "", 204


@chat_bp.route("/sessions/<session_id>/activate", methods=["POST"])
def activate_chat_session(session_id: str):
    chat = _load_chat()
    if get_session(chat, session_id) is None:
        return jsonify({"error": f"Session '{session_id}' not found"}), 404
    set_active_session(chat, session_id)
    _save_chat(chat)
    return jsonify({"message": f"Session '{session_id}' activated"}), 200
