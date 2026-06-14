"""T02.01 + T02.03: Chat-State – Channel-Modell und ChatMessage-v1.

Channel types: room, direct, ai, notes, system
Message fields: id, created_at, channel_id, channel_type, sender_id, sender_kind,
                target_ids, text, visibility, delivery_state, policy_decision_ref
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── Enums ─────────────────────────────────────────────────────────────────────


class ChannelType(str, Enum):
    ROOM = "room"
    DIRECT = "direct"
    AI = "ai"
    NOTES = "notes"
    SYSTEM = "system"


class Visibility(str, Enum):
    LOCAL_ONLY = "local_only"
    ROOM = "room"
    DIRECT = "direct"
    AI_CONTEXT = "ai_context"
    SYSTEM = "system"


class DeliveryState(str, Enum):
    DRAFT = "draft"
    QUEUED = "queued"
    SENT = "sent"
    RECEIVED = "received"
    FAILED = "failed"
    BLOCKED = "blocked"


class SenderKind(str, Enum):
    USER = "user"
    AI = "ai"
    SYSTEM = "system"


# ── Persistence policy ────────────────────────────────────────────────────────


_PERSISTENCE_POLICY: dict[str, str] = {
    ChannelType.ROOM: "hub",
    ChannelType.DIRECT: "hub",
    ChannelType.AI: "local",
    ChannelType.NOTES: "local_only",
    ChannelType.SYSTEM: "ephemeral",
}


# ── Channel factory ───────────────────────────────────────────────────────────


def make_channel(
    channel_id: str,
    channel_type: str,
    display_name: str,
    participants: list[str] | None = None,
    visibility: str | None = None,
) -> dict[str, Any]:
    if visibility is None:
        if channel_type == ChannelType.NOTES:
            visibility = Visibility.LOCAL_ONLY
        elif channel_type == ChannelType.AI:
            visibility = Visibility.AI_CONTEXT
        else:
            visibility = Visibility.ROOM
    return {
        "id": channel_id,
        "channel_type": channel_type,
        "display_name": display_name,
        "visibility": visibility,
        "participants": participants or [],
        "persistence_policy": _PERSISTENCE_POLICY.get(channel_type, "local"),
        "unread": 0,
        "messages": [],
        "scroll_offset": 0,
    }


# ── Default channels ──────────────────────────────────────────────────────────


def default_channels() -> dict[str, dict[str, Any]]:
    return {
        "room:main": make_channel("room:main", ChannelType.ROOM, "#room"),
        "ai:tutor": make_channel("ai:tutor", ChannelType.AI, "AI tutor-ai", participants=["s-ai"]),
        "notes:self": make_channel("notes:self", ChannelType.NOTES, "notes local-only", participants=["self"]),
        "system": make_channel("system", ChannelType.SYSTEM, "system"),
    }


# ── ChatMessage factory ───────────────────────────────────────────────────────


def make_message(
    *,
    channel_id: str,
    channel_type: str,
    sender_id: str,
    text: str,
    sender_kind: str = SenderKind.USER,
    target_ids: list[str] | None = None,
    visibility: str | None = None,
    delivery_state: str = DeliveryState.DRAFT,
    policy_decision_ref: str | None = None,
) -> dict[str, Any]:
    if visibility is None:
        if channel_type == ChannelType.NOTES:
            visibility = Visibility.LOCAL_ONLY
        elif channel_type == ChannelType.AI:
            visibility = Visibility.AI_CONTEXT
        elif channel_type == ChannelType.DIRECT:
            visibility = Visibility.DIRECT
        else:
            visibility = Visibility.ROOM
    return {
        "id": str(uuid.uuid4()),
        "created_at": time.time(),
        "channel_id": channel_id,
        "channel_type": channel_type,
        "sender_id": sender_id,
        "sender_kind": sender_kind,
        "target_ids": target_ids or [],
        "text": str(text)[:500],
        "visibility": visibility,
        "delivery_state": delivery_state,
        "policy_decision_ref": policy_decision_ref,
    }


# ── Chat state (top-level game dict key: "chat_state") ────────────────────────


# ── AI Chat Sessions ─────────────────────────────────────────────────────────
#
# A ChatSession is a named, settings-bound AI conversation thread. Each
# session has its own message history, system prompt, and chat settings
# (chat_backend, code compass scope, source pack, model, ...). The user
# can have multiple sessions in parallel (e.g. "Code-Help", "Writing",
# "General") and switch between them without losing context. Sessions
# are persisted in user.json via user_config_manager.
#
# Each session also appears as an `ai:<session_id>` channel in the
# channels dict for backward compat with the rest of the chat pipeline
# (which expects messages to live in channels).
# ───────────────────────────────────────────────────────────────────────────

# Default settings: applied to new sessions when nothing is configured.
# Mirrors the legacy "user.json scalar settings" so existing behavior
# is preserved for the default sessions.
_DEFAULT_SESSION_SETTINGS: dict[str, Any] = {
    "chat_backend": "ananta-worker",
    "chat_backend_model": "google/gemma-4-e4b",
    "chat_source_pack_id": "ananta-dev-default",
    "chat_use_codecompass": True,
    "chat_codecompass_trigger_mode": "auto",
    "chat_retrieval_profile": "auto",
    "chat_code_questions_repo_first": True,
    "chat_architecture_analysis_mode": False,
    "chat_max_tokens": 4000,
    "chat_answer_chars": 1800,
    "chat_answer_overflow_policy": "allow",
    "chat_never_truncate_answers": True,
    "chat_context_chars": 4000,
    "chat_rag_top_k": 12,
    "chat_history_turns": 6,
    "chat_history_chars": 1800,
    "chat_use_history": True,
    "chat_use_summary": True,
    "chat_summary_chars": 600,
    "chat_include_local_project": True,
    "chat_include_wikipedia": False,
    "chat_include_task_memory": True,
}

# Built-in sessions — these are the templates the user gets out of the box.
# Settings here are DELTAS only — keys that differ from _DEFAULT_SESSION_SETTINGS.
# make_session() merges them with the defaults so the full settings dict is stored.
DEFAULT_SESSIONS: list[dict[str, Any]] = [
    {
        "id": "code-help",
        "name": "Code-Help",
        "icon": "💻",
        "group": "",
        "system_prompt": (
            "You are a focused code assistant for the Ananta project. "
            "When answering, prefer concrete file paths, function names, "
            "and code snippets from the workspace. Be direct and brief. "
            "Use German if the user writes in German."
        ),
        "settings": {
            "chat_backend": "ananta-worker",
            "chat_use_codecompass": True,
            "chat_retrieval_profile": "code_first",
            "chat_code_questions_repo_first": True,
            "chat_architecture_analysis_mode": "rag_iterative",
        },
    },
    {
        "id": "writing-coach",
        "name": "Schreib-Coach",
        "icon": "✍️",
        "group": "",
        "system_prompt": (
            "You are a writing coach. Help the user clarify their thinking, "
            "structure their arguments, and improve their prose. Do not "
            "reference code or project files unless explicitly asked. "
            "Respond in the language the user uses."
        ),
        "settings": {
            "chat_backend": "lmstudio",
            "chat_use_codecompass": False,
            "chat_retrieval_profile": "none",
            "chat_code_questions_repo_first": False,
            "chat_include_local_project": False,
            "chat_include_wikipedia": True,
        },
    },
    {
        "id": "general",
        "name": "Allgemein",
        "icon": "💬",
        "group": "",
        "system_prompt": (
            "You are a helpful, friendly AI assistant. Use the project's "
            "CodeCompass context when it seems relevant, but don't force it. "
            "Match the user's language and tone."
        ),
        "settings": {
            "chat_backend": "ananta-worker",
            "chat_use_codecompass": True,
            "chat_retrieval_profile": "auto",
        },
    },
    # ── Architektur-Gruppe ────────────────────────────────────────────────────
    {
        "id": "arch-overview",
        "name": "Architektur-Überblick",
        "icon": "🏗️",
        "group": "Architektur",
        "system_prompt": (
            "Du bist Architekt des Ananta-Projekts. Der Nutzer beschreibt welchen Teil "
            "des Systems er visualisieren will.\n"
            "Antworte IMMER mit einem Mermaid-Diagramm (flowchart TD oder graph LR).\n"
            "Nutze ausschließlich reale Komponenten, Dateinamen und Module aus dem "
            "bereitgestellten Quellcode-Kontext.\n"
            "Schreibe genau EINE kurze Zeile Beschreibung vor dem Mermaid-Block. "
            "Kein weiterer Text danach."
        ),
        "settings": {
            "chat_architecture_analysis_mode": "rag_iterative",
            "chat_retrieval_profile": "code_first",
            "chat_answer_chars": 5000,
            "chat_use_codecompass": True,
            "chat_code_questions_repo_first": True,
        },
    },
    {
        "id": "arch-classes",
        "name": "Klassen & Interfaces",
        "icon": "🔷",
        "group": "Architektur",
        "system_prompt": (
            "Du bist Architekt. Der Nutzer nennt einen Bereich oder eine Komponente.\n"
            "Antworte IMMER mit einem Mermaid classDiagram.\n"
            "Zeige Klassen, Interfaces, Vererbung und wichtige Methoden/Felder "
            "aus dem Quellcode-Kontext. Nutze reale Klassennamen.\n"
            "Schreibe genau EINE kurze Zeile Beschreibung vor dem Mermaid-Block. "
            "Kein weiterer Text danach."
        ),
        "settings": {
            "chat_architecture_analysis_mode": "rag_iterative",
            "chat_retrieval_profile": "code_first",
            "chat_answer_chars": 5000,
            "chat_use_codecompass": True,
            "chat_code_questions_repo_first": True,
        },
    },
    {
        "id": "arch-sequence",
        "name": "Sequenz & Abläufe",
        "icon": "↔️",
        "group": "Architektur",
        "system_prompt": (
            "Du bist Architekt. Der Nutzer beschreibt einen Ablauf oder Prozess.\n"
            "Antworte IMMER mit einem Mermaid sequenceDiagram.\n"
            "Verwende reale Komponenten, Services und Funktionen als Akteure. "
            "Zeige den tatsächlichen Ablauf aus dem Quellcode-Kontext.\n"
            "Schreibe genau EINE kurze Zeile Beschreibung vor dem Mermaid-Block. "
            "Kein weiterer Text danach."
        ),
        "settings": {
            "chat_architecture_analysis_mode": "rag_iterative",
            "chat_retrieval_profile": "code_first",
            "chat_answer_chars": 5000,
            "chat_use_codecompass": True,
            "chat_code_questions_repo_first": True,
        },
    },
    {
        "id": "arch-deps",
        "name": "Abhängigkeiten",
        "icon": "🔗",
        "group": "Architektur",
        "system_prompt": (
            "Du bist Architekt. Der Nutzer nennt ein Modul oder eine Komponente.\n"
            "Antworte IMMER mit einem Mermaid graph LR Diagramm.\n"
            "Zeige Import- und Abhängigkeitsbeziehungen zwischen Modulen. "
            "Nutze subgraph für Pakete/Namespaces. Verwende reale Datei- und Modulnamen.\n"
            "Schreibe genau EINE kurze Zeile Beschreibung vor dem Mermaid-Block. "
            "Kein weiterer Text danach."
        ),
        "settings": {
            "chat_architecture_analysis_mode": "rag_iterative",
            "chat_retrieval_profile": "code_first",
            "chat_answer_chars": 5000,
            "chat_use_codecompass": True,
            "chat_code_questions_repo_first": True,
        },
    },
]


def make_session(
    *,
    session_id: str,
    name: str,
    system_prompt: str = "",
    settings: dict[str, Any] | None = None,
    icon: str = "💬",
    group: str = "",
) -> dict[str, Any]:
    """Create a new chat session. Settings are merged over the default
    session settings so a session can override individual fields without
    having to repeat the whole defaults table.

    Both the full merged ``settings`` and the raw ``settings_delta`` (only
    the explicitly provided keys) are stored so the frontend can tell which
    values are session-specific overrides vs. inherited defaults."""
    import copy as _copy
    settings_delta: dict[str, Any] = {
        k: v for k, v in (settings or {}).items() if v is not None
    }
    merged_settings = _copy.deepcopy(_DEFAULT_SESSION_SETTINGS)
    for k, v in settings_delta.items():
        merged_settings[k] = v
    return {
        "id": str(session_id),
        "name": str(name or session_id),
        "icon": str(icon or "💬"),
        "group": str(group or ""),
        "system_prompt": str(system_prompt or ""),
        "settings": merged_settings,
        "settings_delta": settings_delta,
        "created_at": time.time(),
        "updated_at": time.time(),
    }


def default_sessions() -> list[dict[str, Any]]:
    """Return a fresh list of the built-in default sessions."""
    return [
        make_session(
            session_id=str(s.get("id") or ""),
            name=str(s.get("name") or s.get("id") or ""),
            system_prompt=str(s.get("system_prompt") or ""),
            settings=dict(s.get("settings") or {}),
            icon=str(s.get("icon") or "💬"),
            group=str(s.get("group") or ""),
        )
        for s in DEFAULT_SESSIONS
    ]


def _ensure_settings_delta(session: dict[str, Any]) -> None:
    """Backfill ``settings_delta`` for sessions created before this field
    existed. Computed by comparing stored settings against the session
    defaults — any key whose value differs is considered an explicit override."""
    if "settings_delta" not in session:
        stored = dict(session.get("settings") or {})
        session["settings_delta"] = {
            k: v for k, v in stored.items()
            if k not in _DEFAULT_SESSION_SETTINGS
            or _DEFAULT_SESSION_SETTINGS.get(k) != v
        }


# ── Session registry inside chat_state ───────────────────────────────────────
# The active chat_state carries the list of sessions. The current
# "active session" is `active_session_id`; the active *channel* stays
# `ai:<active_session_id>`. We keep both for backward compat with the
# existing channel-based message rendering. ──────────────────────────────

def get_sessions(chat: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the list of session dicts. Always non-empty — returns the
    default sessions if the chat state has none yet (legacy / freshly
    initialised).

    Also handles two migration tasks transparently:
    - Backfills ``settings_delta`` for sessions that pre-date that field.
    - Appends any built-in default sessions that are missing (e.g. newly
      added architecture sessions) so users get them automatically."""
    sessions = chat.get("ai_sessions")
    if not isinstance(sessions, list) or not sessions:
        sessions = default_sessions()
        chat["ai_sessions"] = sessions
        return sessions

    # Backfill settings_delta + group for legacy sessions
    for s in sessions:
        if isinstance(s, dict):
            _ensure_settings_delta(s)
            if "group" not in s:
                s["group"] = ""

    # Add missing built-in sessions (e.g. new architecture sessions)
    existing_ids = {str((s or {}).get("id") or "") for s in sessions}
    for default_sess in DEFAULT_SESSIONS:
        sess_id = str(default_sess.get("id") or "")
        if sess_id and sess_id not in existing_ids:
            sessions.append(make_session(
                session_id=sess_id,
                name=str(default_sess.get("name") or sess_id),
                system_prompt=str(default_sess.get("system_prompt") or ""),
                settings=dict(default_sess.get("settings") or {}),
                icon=str(default_sess.get("icon") or "💬"),
                group=str(default_sess.get("group") or ""),
            ))

    chat["ai_sessions"] = sessions
    return sessions


def get_session(chat: dict[str, Any], session_id: str) -> dict[str, Any] | None:
    for s in get_sessions(chat):
        if isinstance(s, dict) and str(s.get("id") or "") == str(session_id):
            return s
    return None


def get_active_session(chat: dict[str, Any]) -> dict[str, Any] | None:
    sessions = get_sessions(chat)
    active_id = str(chat.get("active_session_id") or "")
    for s in sessions:
        if isinstance(s, dict) and str(s.get("id") or "") == active_id:
            return s
    # Fall back to first session
    if sessions and isinstance(sessions[0], dict):
        return sessions[0]
    return None


def active_session_channel_id(chat: dict[str, Any]) -> str:
    """Return the channel_id corresponding to the active session. This is
    the bridge between sessions and the existing channel-based pipeline:
    each session maps 1:1 to an `ai:<session_id>` channel."""
    session = get_active_session(chat)
    if session is None:
        return "ai:tutor"
    return f"ai:{str(session.get('id') or 'tutor')}"


# ── Effective settings bridge ───────────────────────────────────────────────
#
# Settings live in two places:
#
# 1. **Game-level** (top-level `header_logo_game` keys like
#    `chat_backend`, `chat_source_pack_id`, `chat_use_codecompass`).
#    These come from user.json via the operator config and apply to
#    *every* chat operation as a fallback.
#
# 2. **Session-level** (inside an entry of `chat["ai_sessions"]` under
#    `settings`). The active session's settings override the game-level
#    ones, so each session can have its own backend, source pack, etc.
#
# Callers that need the "effective" value of a chat setting should
# call `get_effective_chat_settings(chat, game)` and read the merged
# dict, never the raw `game.get("chat_backend")` style. This keeps
# the per-session override working without scattering session-lookup
# logic across the chat pipeline.

_SESSION_OVERRIDE_KEYS: tuple[str, ...] = (
    "chat_backend",
    "chat_backend_model",
    "chat_backend_api_base",
    "chat_source_pack_id",
    "chat_use_codecompass",
    "chat_retrieval_profile",
    "chat_retrieval_domain_hint",
    "chat_codecompass_trigger_mode",
    "chat_code_questions_repo_first",
    "chat_architecture_analysis_mode",
    "chat_include_task_memory",
    "chat_history_turns",
    "chat_rag_top_k",
    "chat_max_context_chars",
    "chat_system_prompt",
)


def get_effective_chat_settings(
    chat: dict[str, Any],
    game: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the merged chat settings: game-level defaults with the
    active session's per-key overrides applied. The returned dict is
    freshly allocated; callers can mutate it freely. Pass `game` when
    you have it in scope to avoid re-reading the chat_state (the chat
    pipeline typically already has both)."""
    if not isinstance(chat, dict):
        chat = {}
    if not isinstance(game, dict):
        game = {}
    merged: dict[str, Any] = {}
    # 1. Game-level defaults
    for k in _SESSION_OVERRIDE_KEYS:
        if k in game:
            merged[k] = game[k]
    # 2. Session-level overrides
    session = get_active_session(chat)
    if isinstance(session, dict):
        sess_settings = session.get("settings")
        if isinstance(sess_settings, dict):
            for k, v in sess_settings.items():
                if v is None or v == "":
                    # Empty session value — keep the game-level default
                    # so a user can clear a session override by setting
                    # it to "" in the settings UI.
                    continue
                merged[k] = v
        # System prompt from the session itself (not from settings)
        sys_prompt = session.get("system_prompt")
        if isinstance(sys_prompt, str) and sys_prompt:
            merged["chat_system_prompt"] = sys_prompt
    return merged


def active_session_id(chat: dict[str, Any]) -> str:
    """Convenience: return the id of the active session, or "tutor" as
    a last-resort fallback. Used as a stable identifier in session-
    aware chat pipeline messages."""
    session = get_active_session(chat)
    if session is None:
        return "tutor"
    return str(session.get("id") or "tutor")


# ── Session message-history helpers ─────────────────────────────────────────

def clear_session_messages(chat: dict[str, Any], session_id: str | None = None) -> bool:
    """Clear the message list of the named session's channel. If
    `session_id` is None, clear the active session. Returns True if
    a session was found and its channel cleared, False otherwise.

    Only the session's own channel is touched; the other channels
    (room, notes, system) and other sessions' channels are left
    intact. This is the user-facing "clear chat" behaviour: "delete
    the history of this conversation, not all my conversations"."""
    if not isinstance(chat, dict):
        return False
    if session_id is None:
        session = get_active_session(chat)
        if session is None:
            return False
        session_id = str(session.get("id") or "")
    if not session_id:
        return False
    channels = chat.get("channels")
    if not isinstance(channels, dict):
        return False
    channel_id = f"ai:{session_id}"
    ch = channels.get(channel_id)
    if not isinstance(ch, dict):
        return False
    ch["messages"] = []
    ch["unread"] = 0
    return True


def clear_all_session_messages(chat: dict[str, Any]) -> int:
    """Clear the message list of every AI session channel. Used by
    the legacy "clear ALL chat history" command which previously
    cleared every channel indiscriminately. Returns the number of
    sessions whose messages were cleared."""
    if not isinstance(chat, dict):
        return 0
    cleared = 0
    for session in get_sessions(chat):
        if not isinstance(session, dict):
            continue
        sid = str(session.get("id") or "")
        if sid and clear_session_messages(chat, sid):
            cleared += 1
    return cleared


def add_session(chat: dict[str, Any], session: dict[str, Any]) -> None:
    sessions = get_sessions(chat)
    sessions.append(session)
    chat["updated_at"] = time.time()


def delete_session(chat: dict[str, Any], session_id: str) -> bool:
    """Remove a session and its channel. If the deleted session was the
    active one, switch to the first remaining session (or none).

    Refuses to delete the last remaining session — a user must always
    have at least one chat session available. Returns False in that
    case so the UI can show a friendly "letzter session, nicht
    löschbar" message.
    """
    sessions = get_sessions(chat)
    target_id = str(session_id)
    if len(sessions) <= 1:
        return False
    kept = [s for s in sessions if str((s or {}).get("id") or "") != target_id]
    if len(kept) == len(sessions):
        return False
    chat["ai_sessions"] = kept
    # Drop the channel too so the chat pipeline stops trying to write
    # messages into a dead channel.
    channels = chat.get("channels") or {}
    channel_id = f"ai:{target_id}"
    if channel_id in channels:
        try:
            del channels[channel_id]
        except Exception:
            pass
    # Switch active if we just removed the active one
    if str(chat.get("active_session_id") or "") == target_id:
        if kept and isinstance(kept[0], dict):
            chat["active_session_id"] = str(kept[0].get("id") or "")
            chat["active_channel"] = f"ai:{chat['active_session_id']}"
        else:
            chat["active_session_id"] = None
            chat["active_channel"] = "room:main"
    chat["updated_at"] = time.time()
    return True


def set_active_session(chat: dict[str, Any], session_id: str) -> bool:
    """Switch the active session. Mirrors switch_channel() so callers
    don't have to know whether they have a session_id or channel_id.
    Ensures the corresponding channel exists so the chat pipeline can
    immediately write to it."""
    target_id = str(session_id)
    if get_session(chat, target_id) is None:
        return False
    chat["active_session_id"] = target_id
    chat["active_channel"] = f"ai:{target_id}"
    # Make sure the channel exists — important when callers switch to
    # a session that was added after the initial ensure_session_channels
    # call (e.g. a freshly added custom session).
    ensure_session_channels(chat)
    return True


def update_session_settings(
    chat: dict[str, Any],
    session_id: str,
    settings: dict[str, Any],
) -> bool:
    """Merge new settings into a session.

    Values set to ``None`` are treated as "reset to default" — the key is
    removed from ``settings_delta`` and the default value from
    ``_DEFAULT_SESSION_SETTINGS`` is restored in ``settings``.  All other
    values are added to both ``settings`` and ``settings_delta``."""
    import copy as _copy
    session = get_session(chat, session_id)
    if session is None:
        return False
    _ensure_settings_delta(session)
    delta = dict(session.get("settings_delta") or {})
    current = dict(session.get("settings") or {})
    for k, v in (settings or {}).items():
        if v is None:
            delta.pop(k, None)
            if k in _DEFAULT_SESSION_SETTINGS:
                current[k] = _copy.deepcopy(_DEFAULT_SESSION_SETTINGS[k])
            else:
                current.pop(k, None)
        else:
            delta[k] = v
            current[k] = v
    session["settings"] = current
    session["settings_delta"] = delta
    session["updated_at"] = time.time()
    chat["updated_at"] = time.time()
    return True


def ensure_session_channels(chat: dict[str, Any]) -> None:
    """Make sure every session has a corresponding channel. The channel
    is the storage location for messages — the rest of the chat pipeline
    writes to channels, so we keep that abstraction. Sessions that have
    no channel yet get a fresh empty one (preserving any existing
    messages if the channel already exists)."""
    channels = chat.get("channels")
    if not isinstance(channels, dict):
        channels = {}
        chat["channels"] = channels
    for session in get_sessions(chat):
        if not isinstance(session, dict):
            continue
        sid = str(session.get("id") or "")
        if not sid:
            continue
        channel_id = f"ai:{sid}"
        existing = channels.get(channel_id)
        if isinstance(existing, dict):
            # Keep existing messages; just refresh display metadata
            existing["display_name"] = f"{session.get('icon', '💬')} {session.get('name', sid)}"
            existing.setdefault("id", channel_id)
            existing.setdefault("channel_type", ChannelType.AI)
            existing.setdefault("visibility", Visibility.AI_CONTEXT)
            existing.setdefault("participants", ["s-ai"])
            existing.setdefault("persistence_policy", "local")
            existing.setdefault("messages", [])
            existing.setdefault("unread", 0)
            existing.setdefault("scroll_offset", 0)
            continue
        channels[channel_id] = make_channel(
            channel_id=channel_id,
            channel_type=ChannelType.AI,
            display_name=f"{session.get('icon', '💬')} {session.get('name', sid)}",
            participants=["s-ai"],
            visibility=Visibility.AI_CONTEXT,
        )


# ── default_chat_state (updated to include sessions) ────────────────────────

def default_chat_state(local_snake_id: str = "s1") -> dict[str, Any]:
    sessions = default_sessions()
    first_id = str(sessions[0].get("id") or "code-help")
    chat = {
        "local_snake_id": local_snake_id,
        "active_channel": f"ai:{first_id}",
        "active_session_id": first_id,
        "ai_sessions": sessions,
        "channels": default_channels(),
        "chat_focus": False,
        "chat_input_buffer": "",
        "chat_input_cursor": 0,
        "chat_input_history": [],
        "chat_input_history_index": None,
        "chat_input_saved_draft": "",
        "notes_context_released": False,
        "ai_typing": False,
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    # The active channel is ai:<first session>; without this the default
    # state points at a channel that does not exist yet.
    ensure_session_channels(chat)
    return chat


def get_chat_state(game: dict[str, Any]) -> dict[str, Any]:
    raw = game.get("chat_state")
    if isinstance(raw, dict):
        # Migrate legacy state that has no sessions yet — first call after
        # upgrade to the sessions-aware build. We preserve the existing
        # `ai:tutor` channel as the active session so existing chat
        # history is not lost. This is the Backward-Compat shim.
        if not isinstance(raw.get("ai_sessions"), list) or not raw["ai_sessions"]:
            legacy_session = make_session(
                session_id="tutor",
                name="AI Tutor",
                icon="🤖",
                system_prompt="",
                settings={},
            )
            raw["ai_sessions"] = [legacy_session]
            raw["active_session_id"] = "tutor"
            # Re-key existing ai:tutor channel as ai:tutor
            channels = raw.get("channels")
            if not isinstance(channels, dict):
                channels = default_channels()
                raw["channels"] = channels
            elif "ai:tutor" in channels:
                ch = channels.pop("ai:tutor")
                if isinstance(ch, dict):
                    ch["id"] = "ai:tutor"
                    ch.setdefault("display_name", "🤖 AI Tutor")
                    channels["ai:tutor"] = ch
            # Backfill any missing default channels (room, notes, system)
            # so the chat pipeline still has them after the migration.
            for default_ch in default_channels().values():
                cid = str(default_ch.get("id") or "")
                if cid and cid not in channels:
                    channels[cid] = default_ch
            raw.setdefault("active_channel", "ai:tutor")
        # Always make sure each session has a corresponding channel
        ensure_session_channels(raw)
        return raw
    local_id = str(game.get("local_snake_id") or "s1")
    chat = default_chat_state(local_id)
    ensure_session_channels(chat)
    return chat


# ── ChatAnswerBlock ───────────────────────────────────────────────────────────

@dataclass
class SourceRef:
    """A context reference that can be opened in the TUI."""
    ref: str
    display_label: str = ""
    openable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"ref": self.ref, "display_label": self.display_label or self.ref, "openable": self.openable}


@dataclass
class ChatAnswerBlock:
    """Structured heuristic answer, always marked as is_heuristic=True.

    Displayed with '[Heuristik]' / '~' prefix in TUI.
    source_refs are openable (Enter/Click) in TUI.
    """
    result_text: str
    source_refs: list[SourceRef] = field(default_factory=list)
    why_these_sources: str = ""       # max 200 chars
    next_steps: list[str] = field(default_factory=list)  # max 3
    uncertainty_note: str = ""        # set when confidence < 0.7
    is_heuristic: bool = True
    confidence: float = 1.0

    @property
    def tui_prefix(self) -> str:
        return "[Heuristik] " if self.is_heuristic else ""

    @property
    def tui_text(self) -> str:
        return f"{self.tui_prefix}{self.result_text}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_text": self.result_text,
            "tui_text": self.tui_text,
            "source_refs": [r.to_dict() for r in self.source_refs],
            "why_these_sources": self.why_these_sources[:200],
            "next_steps": self.next_steps[:3],
            "uncertainty_note": self.uncertainty_note,
            "is_heuristic": self.is_heuristic,
            "confidence": self.confidence,
        }

    @staticmethod
    def no_good_match() -> "ChatAnswerBlock":
        return ChatAnswerBlock(
            result_text="Kein passender Kontext gefunden.",
            source_refs=[],
            why_these_sources="",
            next_steps=["Frag den AI-Assistenten", "Lade passendes Artefakt"],
            is_heuristic=True,
            confidence=0.0,
        )

    @staticmethod
    def from_decision_result(result: Any, *, confidence: float = 1.0) -> "ChatAnswerBlock":
        """Build from a DecisionResult with answer_kind and selected_context_refs."""
        refs = [SourceRef(ref=r) for r in getattr(result, "selected_context_refs", [])]
        text = "Kontext ausgewählt." if refs else "Kein passender Kontext gefunden."
        note = "" if confidence >= 0.7 else f"Unsicher (confidence={confidence:.2f})"
        return ChatAnswerBlock(
            result_text=text,
            source_refs=refs,
            why_these_sources=f"intent-basierte Selektion, {len(refs)} Refs",
            next_steps=[],
            uncertainty_note=note,
            is_heuristic=True,
            confidence=confidence,
        )


def make_heuristic_message(
    *,
    channel_id: str,
    sender_id: str,
    answer_block: ChatAnswerBlock,
    policy_decision_ref: str | None = None,
) -> dict[str, Any]:
    """Create a chat message carrying a heuristic answer block."""
    msg = make_message(
        channel_id=channel_id,
        channel_type=ChannelType.AI,
        sender_id=sender_id,
        sender_kind=SenderKind.AI,
        text=answer_block.tui_text[:500],
        policy_decision_ref=policy_decision_ref,
    )
    msg["answer_block"] = answer_block.to_dict()
    msg["is_heuristic"] = True
    return msg


def set_chat_state(game: dict[str, Any], chat: dict[str, Any]) -> None:
    game["chat_state"] = chat


# ── Channel helpers ───────────────────────────────────────────────────────────


def get_channel(chat: dict[str, Any], channel_id: str) -> dict[str, Any] | None:
    channels = chat.get("channels") or {}
    return channels.get(channel_id)


def get_active_channel(chat: dict[str, Any]) -> dict[str, Any] | None:
    return get_channel(chat, str(chat.get("active_channel") or "room:main"))


def switch_channel(chat: dict[str, Any], channel_id: str, *, preserve_input: bool = False) -> bool:
    if channel_id not in (chat.get("channels") or {}):
        return False
    ch = chat["channels"][channel_id]
    ch["unread"] = 0
    chat["active_channel"] = channel_id
    chat["scroll_offset"] = 0
    if not preserve_input:
        chat["chat_input_buffer"] = ""
    return True


def add_direct_channel(chat: dict[str, Any], snake_id: str, display_name: str) -> str:
    ch_id = f"direct:{snake_id}"
    if ch_id not in (chat.get("channels") or {}):
        chat.setdefault("channels", {})[ch_id] = make_channel(
            ch_id, ChannelType.DIRECT, f"@{display_name}", participants=[snake_id]
        )
    return ch_id


# ── Message helpers ───────────────────────────────────────────────────────────

_MAX_MESSAGES = 200


def append_message(chat: dict[str, Any], msg: dict[str, Any]) -> None:
    ch_id = str(msg.get("channel_id") or "")
    channels = chat.setdefault("channels", {})
    if ch_id not in channels:
        return
    ch = channels[ch_id]
    msgs: list[dict[str, Any]] = list(ch.get("messages") or [])
    # deduplicate by id
    existing_ids = {m.get("id") for m in msgs}
    if msg.get("id") in existing_ids:
        return
    msgs.append(msg)
    if len(msgs) > _MAX_MESSAGES:
        msgs = msgs[-_MAX_MESSAGES:]
    ch["messages"] = msgs
    # bump unread if channel not active
    if ch_id != chat.get("active_channel"):
        ch["unread"] = int(ch.get("unread") or 0) + 1


def sanitize_text(text: str, *, max_len: int | None = 500) -> str:
    """Strip ANSI and control chars from user-supplied message text."""
    import re
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    cleaned = text.strip()
    if isinstance(max_len, int) and max_len >= 0:
        return cleaned[:max_len]
    return cleaned


def unread_total(chat: dict[str, Any]) -> int:
    return sum(int(ch.get("unread") or 0) for ch in (chat.get("channels") or {}).values())


def maybe_add_prediction_comment(
    chat: dict[str, Any],
    *,
    prediction: dict[str, Any],
    now: float,
    quiet: bool,
    forced: bool = False,
    cooldown_seconds: int = 20,
) -> bool:
    """Add a compact proactive AI comment only when policy gates allow it."""
    if quiet and not forced:
        return False
    confidence = float(prediction.get("confidence") or 0.0)
    if confidence < 0.65 and not forced:
        return False
    last_comment_at = float(chat.get("ai_last_proactive_comment_at") or 0.0)
    if (now - last_comment_at) < max(5, int(cooldown_seconds)) and not forced:
        return False
    intent = str(prediction.get("predicted_intent") or "unknown")
    target_ref = str(prediction.get("target_ref") or "").strip()
    if not target_ref:
        target_ref = "aktuellen Bereich"
    text = f"Ich glaube, du willst zu {target_ref} ({intent}, conf={confidence:.2f})."
    msg = make_message(
        channel_id="ai:tutor",
        channel_type=ChannelType.AI,
        sender_id="s-ai",
        sender_kind=SenderKind.AI,
        text=text,
        delivery_state=DeliveryState.RECEIVED,
    )
    append_message(chat, msg)
    chat["ai_last_proactive_comment_at"] = float(now)
    return True


def append_artifact_graph_explanation(chat: dict[str, Any], *, text: str, goal_id: str) -> None:
    """Persist AI artifact-graph explanation in ai channel and keep it metadata-only."""
    sanitized = sanitize_text(text)
    if not sanitized:
        return
    msg = make_message(
        channel_id="ai:tutor",
        channel_type=ChannelType.AI,
        sender_id="s-ai",
        sender_kind=SenderKind.AI,
        text=f"[artifact-graph:{goal_id}] {sanitized}",
        delivery_state=DeliveryState.RECEIVED,
        visibility=Visibility.AI_CONTEXT,
    )
    append_message(chat, msg)
