from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from flask import Blueprint, jsonify, request

from client_surfaces.operator_tui.chat_state import (
    get_sessions, get_session, add_session, update_session_settings,
    delete_session, make_session, set_active_session, default_conversations,
    default_chat_profiles,
)
from client_surfaces.operator_tui.config.user_config_manager import get_manager

_log = logging.getLogger(__name__)

chat_bp = Blueprint("chat_api", __name__, url_prefix="/api/chat")


def _load_chat() -> dict[str, Any]:
    """Build a minimal chat dict from persisted user.json for session operations."""
    manager = get_manager()
    settings = manager.load()
    sessions = settings.get("chat_sessions") or default_conversations()
    active_id = settings.get("chat_active_session_id") or (sessions[0]["id"] if sessions else "")
    chat = {"ai_sessions": sessions, "active_session_id": active_id, "channels": {}, "_preserve_session_list": True}
    migrated = get_sessions(chat)
    if settings.get("chat_model_version") != 2 or migrated != sessions:
        manager.save({
            "chat_sessions": migrated,
            "chat_active_session_id": active_id,
            "chat_model_version": 2,
        })
    return chat


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


def _load_profiles() -> list[dict[str, Any]]:
    """Load built-in and user profiles, with user profiles stored separately."""
    settings = get_manager().load()
    custom = settings.get("chat_profiles") or []
    custom = custom if isinstance(custom, list) else []
    by_id = {str(profile.get("id") or ""): profile for profile in default_chat_profiles()}
    for profile in custom:
        if isinstance(profile, dict) and profile.get("id"):
            by_id[str(profile["id"])] = dict(profile)
    return list(by_id.values())


def _save_custom_profiles(profiles: list[dict[str, Any]]) -> None:
    get_manager().save({"chat_profiles": profiles})


def _profile_by_id(profile_id: str) -> dict[str, Any] | None:
    return next((profile for profile in _load_profiles() if str(profile.get("id") or "") == profile_id), None)


def _apply_profile(session: dict[str, Any], profile: dict[str, Any]) -> None:
    """Materialize effective profile values while preserving chat overrides."""
    from client_surfaces.operator_tui.chat_state import _DEFAULT_SESSION_SETTINGS

    profile_settings = dict(profile.get("settings") or {})
    effective = dict(_DEFAULT_SESSION_SETTINGS)
    effective.update(profile_settings)
    effective.update(dict(session.get("settings_delta") or {}))
    session["profile_id"] = str(profile.get("id") or "general")
    session["profile_settings"] = profile_settings
    session["profile_system_prompt"] = str(profile.get("system_prompt") or "")
    session["settings"] = effective
    override = str(session.get("system_prompt_override") or "")
    session["system_prompt"] = override or str(profile.get("system_prompt") or "")


# ── Reusable chat profile CRUD ───────────────────────────────────────────────

@chat_bp.route("/profiles", methods=["GET"])
def list_chat_profiles():
    builtin_ids = {str(profile.get("id") or "") for profile in default_chat_profiles()}
    return jsonify([{**profile, "builtin": str(profile.get("id") or "") in builtin_ids} for profile in _load_profiles()])


@chat_bp.route("/profiles", methods=["POST"])
def create_chat_profile():
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()
    profile_id = str(data.get("id") or f"profile-{uuid.uuid4().hex[:12]}").strip()
    if not name or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", profile_id):
        return jsonify({"error": "valid profile id and name are required"}), 400
    if _profile_by_id(profile_id) is not None:
        return jsonify({"error": f"Profile '{profile_id}' already exists"}), 409
    profile = {
        "id": profile_id,
        "name": name,
        "icon": str(data.get("icon") or "🎯"),
        "description": str(data.get("description") or ""),
        "system_prompt": str(data.get("system_prompt") or ""),
        "settings": dict(data.get("settings") or {}),
    }
    custom = list((get_manager().load().get("chat_profiles") or []))
    custom.append(profile)
    _save_custom_profiles(custom)
    return jsonify({**profile, "builtin": False}), 201


@chat_bp.route("/profiles/<profile_id>", methods=["PATCH"])
def update_chat_profile(profile_id: str):
    builtin_ids = {str(profile.get("id") or "") for profile in default_chat_profiles()}
    if profile_id in builtin_ids:
        return jsonify({"error": "built-in profiles are read-only"}), 409
    data = request.get_json(silent=True) or {}
    custom = list((get_manager().load().get("chat_profiles") or []))
    profile = next((p for p in custom if str((p or {}).get("id") or "") == profile_id), None)
    if profile is None:
        return jsonify({"error": f"Profile '{profile_id}' not found"}), 404
    for key in ("name", "icon", "description", "system_prompt"):
        if key in data:
            profile[key] = str(data.get(key) or "")
    if "settings" in data and isinstance(data["settings"], dict):
        profile["settings"] = {**dict(profile.get("settings") or {}), **data["settings"]}
    _save_custom_profiles(custom)
    chat = _load_chat()
    for session in get_sessions(chat):
        if str(session.get("profile_id") or "") == profile_id:
            _apply_profile(session, profile)
    _save_chat(chat)
    return jsonify({**profile, "builtin": False})


@chat_bp.route("/profiles/<profile_id>", methods=["DELETE"])
def delete_chat_profile(profile_id: str):
    builtin_ids = {str(profile.get("id") or "") for profile in default_chat_profiles()}
    if profile_id in builtin_ids:
        return jsonify({"error": "built-in profiles are read-only"}), 409
    chat = _load_chat()
    if any(str(session.get("profile_id") or "") == profile_id for session in get_sessions(chat)):
        return jsonify({"error": "profile is still used by chats"}), 409
    custom = list((get_manager().load().get("chat_profiles") or []))
    kept = [p for p in custom if str((p or {}).get("id") or "") != profile_id]
    if len(kept) == len(custom):
        return jsonify({"error": f"Profile '{profile_id}' not found"}), 404
    _save_custom_profiles(kept)
    return "", 204


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

    profile_id = str(data.get("profile_id") or "general")
    profile = _profile_by_id(profile_id)
    if profile is None:
        return jsonify({"error": f"Profile '{profile_id}' not found"}), 400
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
        profile_id=profile_id,
    )
    _apply_profile(new_session, profile)
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
        session["system_prompt_override"] = str(data["system_prompt"] or "")
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
    if "profile_id" in data:
        profile_id = str(data["profile_id"] or "general")
        profile = _profile_by_id(profile_id)
        if profile is None:
            return jsonify({"error": f"Profile '{profile_id}' not found"}), 400
        _apply_profile(session, profile)
    if "settings" in data and isinstance(data["settings"], dict):
        update_session_settings(chat, session_id, data["settings"])

    profile = _profile_by_id(str(session.get("profile_id") or "general"))
    if profile is not None:
        _apply_profile(session, profile)

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
    folder_id = data.get("id") or f"folder-{uuid.uuid4().hex[:12]}"
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

def _heuristic_reorganize(sessions: list[dict]) -> tuple[list[dict], dict[str, str]]:
    """Group sessions by 'group' (fallback session_type, then 'Allgemein')."""
    group_map: dict[str, list[dict]] = {}
    for s in sessions:
        g = (s.get("group") or s.get("session_type") or "").strip() or "Allgemein"
        group_map.setdefault(g, []).append(s)

    proposed_folders: list[dict] = []
    assignments: dict[str, str] = {}
    for group_name, group_sessions in group_map.items():
        folder_id = f"folder-{uuid.uuid4().hex[:12]}"
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

    return proposed_folders, assignments


def _strip_json_fences(text: str) -> str:
    """Remove markdown code fences (``` / ```json) around an LLM JSON answer."""
    stripped = text.strip()
    match = re.match(r"^```[a-zA-Z0-9_-]*\s*\n?(.*?)\n?```\s*$", stripped, re.DOTALL)
    if match:
        return match.group(1).strip()
    return stripped


def _llm_reorganize(sessions: list[dict]) -> tuple[list[dict], dict[str, str]]:
    """Ask the LLM for a folder proposal. Raises on any failure or invalid output."""
    from agent.services.chat_partial_summary_service import call_llm_text

    session_lines = []
    for s in sessions:
        sp = str(s.get("system_prompt") or "").replace("\n", " ")[:120]
        session_lines.append(
            f"{s.get('id')} | {s.get('name') or ''} | {s.get('session_type') or ''} | "
            f"{s.get('group') or ''} | {sp}"
        )
    example_sid = str(sessions[0].get("id")) if sessions else "session-1"
    prompt = (
        "Du organisierst Chat-Sessions in Ordner. Hier die Sessions "
        "(Format: id | name | type | group | system_prompt-Anfang):\n"
        + "\n".join(session_lines)
        + "\n\nErstelle 2-6 thematische Ordner mit deutschen Namen und passenden Emoji-Icons "
        "und weise jede Session genau einem Ordner zu.\n"
        "Antworte NUR mit striktem JSON in exakt diesem Format, ohne Erklärungen:\n"
        '{"folders": [{"id": "f1", "name": "...", "icon": "📁", "parent_id": ""}], '
        f'"assignments": {{"{example_sid}": "f1"}}}}\n'
        "Verwende in assignments die echten Session-IDs als Schlüssel (jede genau einmal, "
        "zeichengenau kopiert, NICHT übersetzen oder umbenennen) und die Ordner-IDs "
        "(f1, f2, ...) als Werte.\n"
        "Gültige Session-IDs: "
        + ", ".join(str(s.get("id")) for s in sessions)
    )

    raw = call_llm_text(prompt, timeout=30)
    if not raw:
        raise ValueError("empty LLM response")

    parsed = json.loads(_strip_json_fences(raw))
    if not isinstance(parsed, dict):
        raise ValueError("LLM response is not a JSON object")
    raw_folders = parsed.get("folders")
    raw_assignments = parsed.get("assignments")
    if not isinstance(raw_folders, list) or not raw_folders:
        raise ValueError("invalid 'folders' in LLM response")
    if not isinstance(raw_assignments, dict) or not raw_assignments:
        raise ValueError("invalid 'assignments' in LLM response")

    # Build proposed-id → final-uuid-id mapping and validated folder dicts.
    session_ids = {str(s.get("id")) for s in sessions}
    id_map: dict[str, str] = {}
    folders: list[dict] = []
    for f in raw_folders:
        if not isinstance(f, dict):
            raise ValueError("folder entry is not an object")
        proposed_id = str(f.get("id") or "").strip()
        name = str(f.get("name") or "").strip()
        if not proposed_id or not name:
            raise ValueError("folder entry missing id or name")
        if proposed_id in id_map:
            raise ValueError(f"duplicate folder id '{proposed_id}'")
        id_map[proposed_id] = f"folder-{uuid.uuid4().hex[:12]}"
        folders.append({
            "id": id_map[proposed_id],
            "name": name,
            "icon": str(f.get("icon") or "📁"),
            "parent_id": str(f.get("parent_id") or "").strip(),
            "color": "",
        })
    # Remap parent_id references (proposed ids → final ids; unknown refs → root).
    for f in folders:
        f["parent_id"] = id_map.get(f["parent_id"], "")

    assignments: dict[str, str] = {}
    for sid, fid in raw_assignments.items():
        sid = str(sid)
        fid = str(fid)
        if sid not in session_ids:
            raise ValueError(f"assignment references unknown session '{sid}'")
        if fid not in id_map:
            raise ValueError(f"assignment references unknown folder '{fid}'")
        assignments[sid] = id_map[fid]

    return folders, assignments


@chat_bp.route("/sessions/ai-reorganize", methods=["POST"])
def ai_reorganize_sessions():
    """Propose a folder structure based on current sessions (LLM first, heuristic fallback).

    Returns a proposal the user can preview and optionally accept via
    separate PATCH calls. No state is modified by this endpoint.
    """
    chat = _load_chat()
    sessions = get_sessions(chat)

    method = "heuristic"
    try:
        proposed_folders, assignments = _llm_reorganize(sessions)
        method = "llm"
    except Exception as exc:  # noqa: BLE001 — any LLM failure falls back
        _log.debug("ai-reorganize LLM proposal failed, using heuristic: %s", exc)
        proposed_folders, assignments = _heuristic_reorganize(sessions)

    label = "KI" if method == "llm" else "Heuristik"
    return jsonify({
        "folders": proposed_folders,
        "assignments": assignments,
        "method": method,
        "summary": f"Vorschlag ({label}): {len(proposed_folders)} Ordner für {len(sessions)} Sessions",
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


# ── Partial summary ───────────────────────────────────────────────────────────

@chat_bp.route("/sessions/<session_id>/summarize", methods=["POST"])
def summarize_session_messages(session_id: str):
    """Summarize a user-selected slice of chat messages (LLM, extractive fallback)."""
    from agent.services.chat_partial_summary_service import get_chat_partial_summary_service

    chat = _load_chat()
    session = get_session(chat, session_id)
    if session is None:
        return jsonify({"error": f"Session '{session_id}' not found"}), 404

    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return jsonify({"error": "Invalid request body"}), 400

    messages = data.get("messages")
    if not isinstance(messages, list) or not messages:
        return jsonify({"error": "messages must be a non-empty list"}), 400
    if not all(isinstance(m, dict) and str(m.get("text") or "").strip() for m in messages):
        return jsonify({"error": "each message must be an object with a non-empty 'text'"}), 400

    settings = session.get("settings") or {}
    target_chars = data.get("target_chars")
    if target_chars is None:
        target_chars = settings.get("chat_partial_summary_chars") or 800
    try:
        target_chars = int(target_chars)
    except (TypeError, ValueError):
        return jsonify({"error": "target_chars must be an integer"}), 400

    instruction = str(data.get("instruction") or "")

    result = get_chat_partial_summary_service().summarize(
        messages, target_chars=target_chars, instruction=instruction,
    )
    return jsonify({
        "summary": result.summary,
        "method": result.method,
        "source_count": result.source_count,
        "chars": result.chars,
    })


# ── Prompt-assembly preview ───────────────────────────────────────────────────

@chat_bp.route("/sessions/<session_id>/prompt-preview", methods=["POST"])
def preview_session_prompt(session_id: str):
    """Preview how the next prompt would be assembled from the session settings.

    Pure/read-only: mirrors the real assembly pipeline without modifying state.
    History lives client-side, so it arrives in the request body.
    """
    chat = _load_chat()
    session = get_session(chat, session_id)
    if session is None:
        return jsonify({"error": f"Session '{session_id}' not found"}), 404

    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return jsonify({"error": "Invalid request body"}), 400
    message = str(data.get("message") or "")
    if not message.strip():
        return jsonify({"error": "message is required"}), 400

    settings = session.get("settings") or {}

    # ── system_prompt ────────────────────────────────────────────────────
    system_prompt = str(session.get("system_prompt") or "")

    # ── summary ──────────────────────────────────────────────────────────
    use_summary = bool(settings.get("chat_use_summary", True))
    summary_chars = int(settings.get("chat_summary_chars") or 600)
    raw_summary = str(data.get("summary") or "")
    summary_text = ""
    summary_truncated = False
    if use_summary and raw_summary:
        summary_text = raw_summary[:summary_chars]
        summary_truncated = len(raw_summary) > summary_chars

    # ── history ──────────────────────────────────────────────────────────
    use_history = bool(settings.get("chat_use_history", True))
    history_turns = int(settings.get("chat_history_turns") or 6)
    history_chars = int(settings.get("chat_history_chars") or 1800)
    raw_history = data.get("history") if isinstance(data.get("history"), list) else []
    history_text = ""
    history_truncated = False
    if use_history and raw_history:
        entries = [e for e in raw_history if isinstance(e, dict)]
        kept = entries[-history_turns:] if history_turns > 0 else []
        history_truncated = len(kept) < len(entries)
        joined = "\n".join(
            f"{str(e.get('sender') or '')}: {str(e.get('text') or '')}" for e in kept
        )
        if len(joined) > history_chars:
            # Truncate from the front — keep the newest end.
            joined = joined[-history_chars:]
            history_truncated = True
        history_text = joined

    # ── rag placeholder ──────────────────────────────────────────────────
    use_rag = bool(settings.get("chat_use_codecompass", True))
    retrieval_profile = str(settings.get("chat_retrieval_profile") or "auto")
    rag_top_k = int(settings.get("chat_rag_top_k") or 12)
    context_chars = int(settings.get("chat_context_chars") or 4000)
    rag_text = (
        f"(RAG-Kontext wird zur Laufzeit abgerufen — Profil: {retrieval_profile}, "
        f"Top-K: {rag_top_k}, max. {context_chars} Zeichen)"
    )

    sections = [
        {
            "name": "system_prompt",
            "enabled": True,
            "chars": len(system_prompt),
            "truncated": False,
            "text": system_prompt,
        },
        {
            "name": "summary",
            "enabled": use_summary,
            "chars": len(summary_text),
            "truncated": summary_truncated,
            "text": summary_text,
        },
        {
            "name": "history",
            "enabled": use_history,
            "chars": len(history_text),
            "truncated": history_truncated,
            "text": history_text,
        },
        {
            "name": "rag",
            "enabled": use_rag,
            "chars": context_chars if use_rag else 0,
            "truncated": False,
            "text": rag_text,
        },
        {
            "name": "user_message",
            "enabled": True,
            "chars": len(message),
            "truncated": False,
            "text": message,
        },
    ]

    headers = {
        "system_prompt": "## System-Prompt",
        "summary": "## Zusammenfassung",
        "history": "## Verlauf",
        "rag": "## Kontext (RAG)",
        "user_message": "## Neue Nachricht",
    }
    blocks = [
        f"{headers[s['name']]}\n{s['text']}"
        for s in sections
        if s["enabled"] and s["text"]
    ]
    assembled_prompt = "\n\n".join(blocks)

    return jsonify({
        "session_id": session_id,
        "sections": sections,
        "total_chars": len(assembled_prompt),
        "assembled_prompt": assembled_prompt,
    })
