"""AI-assisted chat endpoints: reorganize proposal, context overview, partial summary, prompt preview."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from flask import (
    jsonify,
    request,
)

from agent.auth import check_user_auth
from agent.routes.chat_blueprint import chat_bp
from agent.routes.chat_route_access import (
    _chat_workflow_principal,
    _log,
    _organization_service,
    _owned_session,
    _owned_sessions,
    _require_global_chat_admin,
)
from agent.routes.chat_route_persistence import (
    _load_chat,
    _load_folders,
    _save_chat,
)
from agent.services.chat_session_security import chat_session_mutation_lock
from agent.services.chat_setting_catalog import effective_chat_context_chars

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
        proposed_folders.append(
            {
                "id": folder_id,
                "name": group_name,
                "icon": icon,
                "parent_id": "",
                "color": "",
            }
        )
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


def _llm_reorganize(
    sessions: list[dict],
    folders: list[dict],
    *,
    input_policy: str = "metadata_only",
) -> tuple[list[dict], dict[str, str]]:
    """Ask the LLM for a folder proposal. Raises on any failure or invalid output."""
    from agent.services.chat_partial_summary_service import call_llm_text

    folder_by_id = {str(item.get("id") or ""): item for item in folders}

    def folder_path(folder_id: str) -> str:
        names: list[str] = []
        seen: set[str] = set()
        while folder_id and folder_id not in seen:
            seen.add(folder_id)
            folder = folder_by_id.get(folder_id)
            if folder is None:
                break
            names.append(str(folder.get("name") or folder_id))
            folder_id = str(folder.get("parent_id") or "")
        return "/".join(reversed(names))

    session_lines = []
    remaining_context_chars = 12000
    for s in sessions:
        preview = ""
        if input_policy == "metadata_plus_preview":
            preview = str(s.get("last_message_preview") or "").replace("\n", " ")[:160]
        line = (
            f"{s.get('id')} | {s.get('name') or ''} | {s.get('session_type') or ''} | "
            f"{s.get('session_subtype') or ''} | {s.get('profile_id') or ''} | "
            f"{s.get('group') or ''} | {folder_path(str(s.get('folder_id') or ''))} | "
            f"{int(s.get('message_count') or 0)} | {preview}"
        )
        if remaining_context_chars <= 0:
            break
        session_lines.append(line[:remaining_context_chars])
        remaining_context_chars -= len(session_lines[-1]) + 1
    example_sid = str(sessions[0].get("id")) if sessions else "session-1"
    prompt = (
        "Du organisierst Chat-Sessions in Ordner. Hier die Sessions "
        "Profile beschreibt die Arbeitsweise, Type/Subtype das Thema und Folder nur die Organisation. "
        "Bewahre sinnvolle bestehende Struktur und schlage nur notwendige Aenderungen vor.\n"
        "Format: id | name | type | subtype | profile | legacy_group | folder_path | "
        "message_count | begrenzte_preview:\n"
        + "\n".join(session_lines)
        + "\n\nErstelle 2-6 thematische Ordner mit deutschen Namen und passenden Emoji-Icons "
        "und weise jede Session genau einem Ordner zu.\n"
        "Antworte NUR mit striktem JSON in exakt diesem Format, ohne Erklärungen:\n"
        '{"folders": [{"id": "f1", "name": "...", "icon": "📁", "parent_id": ""}], '
        f'"assignments": {{"{example_sid}": "f1"}}}}\n'
        "Verwende in assignments die echten Session-IDs als Schlüssel (jede genau einmal, "
        "zeichengenau kopiert, NICHT übersetzen oder umbenennen) und die Ordner-IDs "
        "(f1, f2, ...) als Werte.\n"
        "Gültige Session-IDs: " + ", ".join(str(s.get("id")) for s in sessions)
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
        folders.append(
            {
                "id": id_map[proposed_id],
                "name": name,
                "icon": str(f.get("icon") or "📁"),
                "parent_id": str(f.get("parent_id") or "").strip(),
                "color": "",
            }
        )
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
@check_user_auth
@_require_global_chat_admin
def ai_reorganize_sessions():
    """Propose a folder structure based on current sessions (LLM first, heuristic fallback).

    Returns a proposal the user can preview and optionally accept via
    separate PATCH calls. No state is modified by this endpoint.
    """
    principal = _chat_workflow_principal()
    if principal is None:
        return jsonify({"error": "forbidden", "error_code": "forbidden"}), 403
    with chat_session_mutation_lock:
        chat = _load_chat(principal=principal)
        sessions, migrated = _owned_sessions(chat, principal)
        if migrated:
            _save_chat(chat, principal=principal)
    folders = _load_folders()
    data = request.get_json(silent=True) or {}
    input_policy = str(data.get("input_policy") or "metadata_only")
    if input_policy not in {"metadata_only", "metadata_plus_preview"}:
        return jsonify({"error": "unsupported input policy", "error_code": "input_policy_invalid"}), 400

    method = "heuristic"
    try:
        proposed_folders, assignments = _llm_reorganize(sessions, folders, input_policy=input_policy)
        method = "llm"
    except Exception as exc:  # noqa: BLE001 — any LLM failure falls back
        _log.debug("ai-reorganize LLM proposal failed, using heuristic: %s", exc)
        proposed_folders, assignments = _heuristic_reorganize(sessions)

    label = "KI" if method == "llm" else "Heuristik"
    operations: list[dict[str, Any]] = []
    proposed_ids = {str(folder.get("id") or "") for folder in proposed_folders}
    for folder in proposed_folders:
        folder_id = str(folder.get("id") or "")
        operations.append(
            {
                "operation_id": f"create-{folder_id}",
                "type": "folder.create",
                "temp_id": folder_id,
                "after": {key: folder.get(key) for key in ("name", "icon", "color", "parent_id")},
                "rationale": "Thematische Gruppierung",
            }
        )
    for session_id, folder_id in assignments.items():
        if folder_id in proposed_ids:
            operations.append(
                {
                    "operation_id": f"move-{session_id}",
                    "type": "conversation.move",
                    "target_id": session_id,
                    "after": folder_id,
                    "rationale": "Conversation dem vorgeschlagenen Themenordner zuordnen",
                }
            )
    service = _organization_service()
    proposal = service.create_proposal(
        {
            "source": "ai",
            "method": method,
            "input_policy": input_policy,
            "summary": f"Vorschlag ({label}): {len(proposed_folders)} Ordner für {len(sessions)} Sessions",
            "operations": operations,
        }
    )
    proposal = service.validate_proposal(proposal["id"])
    return jsonify(
        {
            **proposal,
            "folders": proposed_folders,
            "assignments": assignments,
            "method": method,
            "summary": proposal["summary"],
        }
    )


# ── Context overview ──────────────────────────────────────────────────────────


@chat_bp.route("/sessions/<session_id>/context-overview", methods=["GET"])
@check_user_auth
def get_session_context_overview(session_id: str):
    """Return a breakdown of what goes into the next prompt for a given session."""
    principal = _chat_workflow_principal()
    if principal is None:
        return jsonify({"error": "forbidden", "error_code": "forbidden"}), 403
    with chat_session_mutation_lock:
        chat = _load_chat(principal=principal)
        session, migrated = _owned_session(chat, session_id, principal)
        if migrated:
            _save_chat(chat, principal=principal)
    if session is None:
        return jsonify({"error": f"Session '{session_id}' not found"}), 404

    settings = session.get("settings") or {}
    sp = str(session.get("system_prompt") or "")
    return jsonify(
        {
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
                "max_chars": effective_chat_context_chars(settings),
            },
        }
    )


# ── Partial summary ───────────────────────────────────────────────────────────


@chat_bp.route("/sessions/<session_id>/summarize", methods=["POST"])
@check_user_auth
def summarize_session_messages(session_id: str):
    """Summarize a user-selected slice of chat messages (LLM, extractive fallback)."""
    from agent.services.chat_partial_summary_service import get_chat_partial_summary_service

    principal = _chat_workflow_principal()
    if principal is None:
        return jsonify({"error": "forbidden", "error_code": "forbidden"}), 403
    with chat_session_mutation_lock:
        chat = _load_chat(principal=principal)
        session, migrated = _owned_session(chat, session_id, principal)
        if migrated:
            _save_chat(chat, principal=principal)
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
        messages,
        target_chars=target_chars,
        instruction=instruction,
    )
    return jsonify(
        {
            "summary": result.summary,
            "method": result.method,
            "source_count": result.source_count,
            "chars": result.chars,
        }
    )


# ── Prompt-assembly preview ───────────────────────────────────────────────────


@chat_bp.route("/sessions/<session_id>/prompt-preview", methods=["POST"])
@check_user_auth
def preview_session_prompt(session_id: str):
    """Preview how the next prompt would be assembled from the session settings.

    Pure/read-only: mirrors the real assembly pipeline without modifying state.
    History lives client-side, so it arrives in the request body.
    """
    principal = _chat_workflow_principal()
    if principal is None:
        return jsonify({"error": "forbidden", "error_code": "forbidden"}), 403
    with chat_session_mutation_lock:
        chat = _load_chat(principal=principal)
        session, migrated = _owned_session(chat, session_id, principal)
        if migrated:
            _save_chat(chat, principal=principal)
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
        joined = "\n".join(f"{str(e.get('sender') or '')}: {str(e.get('text') or '')}" for e in kept)
        if len(joined) > history_chars:
            # Truncate from the front — keep the newest end.
            joined = joined[-history_chars:]
            history_truncated = True
        history_text = joined

    # ── rag placeholder ──────────────────────────────────────────────────
    use_rag = bool(settings.get("chat_use_codecompass", True))
    retrieval_profile = str(settings.get("chat_retrieval_profile") or "auto")
    rag_top_k = int(settings.get("chat_rag_top_k") or 12)
    context_chars = effective_chat_context_chars(settings)
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
    blocks = [f"{headers[s['name']]}\n{s['text']}" for s in sections if s["enabled"] and s["text"]]
    assembled_prompt = "\n\n".join(blocks)

    return jsonify(
        {
            "session_id": session_id,
            "sections": sections,
            "total_chars": len(assembled_prompt),
            "assembled_prompt": assembled_prompt,
        }
    )
