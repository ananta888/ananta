from __future__ import annotations

import logging
from typing import Any

from flask import Blueprint, jsonify, request

from client_surfaces.operator_tui.chat_state import (
    get_sessions, get_session, add_session, update_session_settings,
    delete_session, make_session, set_active_session, default_sessions,
)
from client_surfaces.operator_tui.config.user_config_manager import get_manager

_log = logging.getLogger(__name__)

chat_bp = Blueprint("chat_api", __name__, url_prefix="/api/chat")


def _load_chat() -> dict[str, Any]:
    """Build a minimal chat dict from persisted user.json for session operations."""
    settings = get_manager().load()
    sessions = settings.get("chat_sessions") or default_sessions()[:3]
    active_id = settings.get("chat_active_session_id") or (sessions[0]["id"] if sessions else "")
    return {"ai_sessions": sessions, "active_session_id": active_id, "channels": {}, "_preserve_session_list": True}


def _save_chat(chat: dict[str, Any]) -> None:
    """Persist sessions back to user.json."""
    get_manager().save({
        "chat_sessions": chat.get("ai_sessions") or [],
        "chat_active_session_id": chat.get("active_session_id") or "",
    })


def _load_folders() -> list[dict]:
    """Load chat_folders from user.json."""
    settings = get_manager().load()
    raw = settings.get("chat_folders") or []
    return raw if isinstance(raw, list) else []


def _save_folders(folders: list[dict]) -> None:
    """Persist chat_folders to user.json (merging with existing keys)."""
    get_manager().save({"chat_folders": folders})


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
        folder_id=data.get("folder_id", ""),
        session_type=data.get("session_type", ""),
        type_description=data.get("type_description", ""),
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
    if "folder_id" in data:
        session["folder_id"] = str(data["folder_id"] or "")
    if "session_type" in data:
        session["session_type"] = str(data["session_type"] or "")
    if "type_description" in data:
        session["type_description"] = str(data["type_description"] or "")
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


# ── Folder CRUD ──────────────────────────────────────────────────────────────

@chat_bp.route("/folders", methods=["GET"])
def list_folders():
    return jsonify(_load_folders())


@chat_bp.route("/folders", methods=["POST"])
def create_folder():
    import time as _time
    data = request.json
    if not data or not isinstance(data, dict):
        return jsonify({"error": "Invalid request body"}), 400
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    folder_id = data.get("id") or f"folder-{int(_time.time()*1000)}"
    folders = _load_folders()
    if any(f.get("id") == folder_id for f in folders):
        return jsonify({"error": f"Folder '{folder_id}' already exists"}), 409
    folder = {
        "id": folder_id,
        "name": name,
        "icon": str(data.get("icon") or "📁"),
        "parent_id": str(data.get("parent_id") or ""),
        "color": str(data.get("color") or ""),
        "created_at": _time.time(),
        "updated_at": _time.time(),
    }
    folders.append(folder)
    _save_folders(folders)
    return jsonify(folder), 201


@chat_bp.route("/folders/<folder_id>", methods=["PATCH"])
def update_folder(folder_id: str):
    import time as _time
    folders = _load_folders()
    folder = next((f for f in folders if f.get("id") == folder_id), None)
    if folder is None:
        return jsonify({"error": f"Folder '{folder_id}' not found"}), 404
    data = request.json or {}
    if "name" in data:
        folder["name"] = str(data["name"] or "").strip() or folder["name"]
    if "icon" in data:
        folder["icon"] = str(data["icon"] or "📁")
    if "parent_id" in data:
        folder["parent_id"] = str(data["parent_id"] or "")
    if "color" in data:
        folder["color"] = str(data["color"] or "")
    folder["updated_at"] = _time.time()
    _save_folders(folders)
    return jsonify(folder)


@chat_bp.route("/folders/<folder_id>", methods=["DELETE"])
def delete_folder(folder_id: str):
    folders = _load_folders()
    if not any(f.get("id") == folder_id for f in folders):
        return jsonify({"error": f"Folder '{folder_id}' not found"}), 404
    folders = [f for f in folders if f.get("id") != folder_id]
    _save_folders(folders)
    # Move sessions in this folder to root
    chat = _load_chat()
    sessions = get_sessions(chat)
    for s in sessions:
        if s.get("folder_id") == folder_id:
            s["folder_id"] = ""
    _save_chat(chat)
    return "", 204


# ── AI Reorganize ─────────────────────────────────────────────────────────────

@chat_bp.route("/sessions/ai-reorganize", methods=["POST"])
def ai_reorganize_sessions():
    """Propose a folder structure based on current session groups/types.

    Returns a proposal the user can preview and optionally accept via
    separate PATCH calls. No state is modified by this endpoint.
    """
    import time as _time
    chat = _load_chat()
    sessions = get_sessions(chat)

    # Group sessions by their 'group' field, falling back to session_type, then 'Allgemein'
    group_map: dict[str, list[dict]] = {}
    for s in sessions:
        g = (s.get("group") or s.get("session_type") or "").strip() or "Allgemein"
        group_map.setdefault(g, []).append(s)

    ts = int(_time.time() * 1000)
    proposed_folders: list[dict] = []
    assignments: dict[str, str] = {}
    for i, (group_name, group_sessions) in enumerate(group_map.items()):
        folder_id = f"folder-{group_name.lower().replace(' ', '-')[:20]}-{ts + i}"
        icon = "📁"
        if "arch" in group_name.lower() or "architektur" in group_name.lower():
            icon = "🏗️"
        elif "konfig" in group_name.lower() or "config" in group_name.lower():
            icon = "⚙️"
        elif "schreib" in group_name.lower() or "writing" in group_name.lower():
            icon = "✍️"
        elif "code" in group_name.lower():
            icon = "💻"
        proposed_folders.append({
            "id": folder_id,
            "name": group_name,
            "icon": icon,
            "parent_id": "",
            "color": "",
        })
        for s in group_sessions:
            assignments[s["id"]] = folder_id

    return jsonify({
        "folders": proposed_folders,
        "assignments": assignments,
        "summary": f"Vorschlag: {len(proposed_folders)} Ordner für {len(sessions)} Sessions",
    })


# ── Context overview ──────────────────────────────────────────────────────────

@chat_bp.route("/sessions/<session_id>/context-overview", methods=["GET"])
def get_session_context_overview(session_id: str):
    """Return a breakdown of what goes into the next prompt for a given session."""
    chat = _load_chat()
    session = get_session(chat, session_id)
    if session is None:
        return jsonify({"error": f"Session '{session_id}' not found"}), 404

    settings = session.get("settings") or {}
    sp = str(session.get("system_prompt") or "")
    return jsonify({
        "session_id": session_id,
        "system_prompt": {
            "text": sp[:200] + ("…" if len(sp) > 200 else ""),
            "chars": len(sp),
            "enabled": True,
        },
        "history": {
            "enabled": bool(settings.get("chat_use_history", True)),
            "max_turns": int(settings.get("chat_history_turns") or 6),
            "max_chars": int(settings.get("chat_history_chars") or 1800),
        },
        "summary": {
            "enabled": bool(settings.get("chat_use_summary", True)),
            "max_chars": int(settings.get("chat_summary_chars") or 600),
        },
        "rag": {
            "enabled": bool(settings.get("chat_use_codecompass", True)),
            "profile": str(settings.get("chat_retrieval_profile") or "auto"),
            "top_k": int(settings.get("chat_rag_top_k") or 12),
            "max_chars": int(settings.get("chat_context_chars") or 4000),
        },
    })
