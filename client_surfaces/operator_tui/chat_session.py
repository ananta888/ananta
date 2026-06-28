"""chat_session.py — AI chat session management.

Contains: session defaults, session CRUD, session↔channel bridge,
          effective-settings merge, and message-history helpers.
"""
from __future__ import annotations

import time
from typing import Any

from client_surfaces.operator_tui.chat_models import ChannelType, Visibility
from client_surfaces.operator_tui.chat_channel import make_channel


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
    "chat_read_only": False,  # when True: session is a backend-managed log; user cannot post
    # ── Predictive UI Guide (PUG) settings ────────────────────────────────
    # When predictive_guide_enabled is True, the visual snake observes the
    # DOM snapshot and proactively suggests guide steps. All 7 keys are
    # tunable from the "Predictive Guide" tab in the Ananta-Konfig sidebar.
    # The master toggle defaults to False — the user must opt in.
    "predictive_guide_enabled": False,
    "predictive_guide_mode": "balanced",   # "quiet" | "balanced" | "eager" | "custom"
    "predictive_guide_dwell_ms": 1500,     # stability window before a prediction fires
    "predictive_guide_min_confidence": 0.55,  # CONFIRMED threshold (0.0–1.0)
    "predictive_guide_ttl_seconds": 20,    # prediction lifetime before it expires
    "predictive_guide_multi_candidates": 3,  # 1–5 alternative DSL candidates per tick
    "predictive_guide_log_deltas_only": True,  # log only changes between snapshots
}


#: Canonical list of every PUG setting key. Single source of truth for
#: the frontend, the backend, the migration / preset helpers, and tests.
PREDICTIVE_GUIDE_KEYS: tuple[str, ...] = (
    "predictive_guide_enabled",
    "predictive_guide_mode",
    "predictive_guide_dwell_ms",
    "predictive_guide_min_confidence",
    "predictive_guide_ttl_seconds",
    "predictive_guide_multi_candidates",
    "predictive_guide_log_deltas_only",
)


# ── Predictive UI Guide presets ───────────────────────────────────────────────
# Each preset is a full delta over _DEFAULT_SESSION_SETTINGS covering all 7
# PUG keys. Selecting a preset overwrites all of them; users can then tweak
# individual keys and the preset is reported as "custom".

PRESET_PREDICTIVE_QUIET: dict[str, Any] = {
    "predictive_guide_enabled": True,
    "predictive_guide_mode": "quiet",
    "predictive_guide_dwell_ms": 3000,         # 3s stability
    "predictive_guide_min_confidence": 0.75,   # high bar
    "predictive_guide_ttl_seconds": 10,        # short memory
    "predictive_guide_multi_candidates": 1,    # primary only
    "predictive_guide_log_deltas_only": True,
}

PRESET_PREDICTIVE_BALANCED: dict[str, Any] = {
    "predictive_guide_enabled": True,
    "predictive_guide_mode": "balanced",
    "predictive_guide_dwell_ms": 1500,
    "predictive_guide_min_confidence": 0.55,
    "predictive_guide_ttl_seconds": 20,
    "predictive_guide_multi_candidates": 3,
    "predictive_guide_log_deltas_only": True,
}

PRESET_PREDICTIVE_EAGER: dict[str, Any] = {
    "predictive_guide_enabled": True,
    "predictive_guide_mode": "eager",
    "predictive_guide_dwell_ms": 500,          # fast trigger
    "predictive_guide_min_confidence": 0.35,   # low bar
    "predictive_guide_ttl_seconds": 30,        # longer memory
    "predictive_guide_multi_candidates": 4,    # show alternatives
    "predictive_guide_log_deltas_only": False, # full snapshot
}

PREDICTIVE_PRESETS: dict[str, dict[str, Any]] = {
    "quiet": PRESET_PREDICTIVE_QUIET,
    "balanced": PRESET_PREDICTIVE_BALANCED,
    "eager": PRESET_PREDICTIVE_EAGER,
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
    # ── Konfiguration-Gruppe ─────────────────────────────────────────────────
    {
        "id": "ananta-settings",
        "name": "Ananta-Konfig",
        "icon": "⚙️",
        "group": "Konfiguration",
        "system_prompt": (
            "Du bist UI- und Konfigurations-Guide für Ananta. Du hast Zugriff auf Tools, die du aktiv nutzen sollst.\n\n"
            "TOOL-REGELN (WICHTIG – zwingend einhalten):\n"
            "- Rufe search_ui_docs() IMMER auf, bevor du einen Begriff, ein Konzept, ein Feld oder eine Funktion erklärst, über die du nicht 100% sicher bist\n"
            "- Antworte NIE mit Vermutungen oder generischen Erklärungen – suche ERST, dann antworte\n"
            "- Bei Folgefragen oder unklaren Begriffen (z.B. 'was bedeutet X?', 'wie geht Y?') gilt: ERST search_ui_docs(), DANN antworten\n"
            "- Nutze get_team_types() wenn der Nutzer nach Team-Typen oder Basis-Team-Typ fragt\n"
            "- Nutze get_hub_workers() für Worker-Status, get_hub_policies() für ausstehende Genehmigungen\n\n"
            "ANTWORT-STIL:\n"
            "- Erkläre SCHRITT FÜR SCHRITT, welche Menüpunkte und Buttons der Nutzer klicken muss\n"
            "- Bei Konzept-Fragen: erst kurze Erklärung, dann UI-Schritte\n"
            "- Antworte auf Deutsch, knapp und konkret\n\n"
            "Bekannte UI-Bereiche in Ananta:\n"
            "- 'AI Chats' (Menü oben): Chat-Sessions verwalten, Backend/Modell pro Session wählen\n"
            "- AI-Snake-Panel (🐍-Button): Chat, Sessions, Trace, Pair Dev, Einstellungen\n"
            "- 'Control Center' (Menü): Workers, Sessions, Policies, CodeCompass\n"
            "- 'Dashboard': Ziele & Tasks\n"
            "- 'Teams & Blueprints': im Control Center → Blueprints erstellen, Team-Typen verwalten\n"
            "- Pair Dev: im AI-Snake-Panel → Tab 'Pair Dev' → Share-Code eingeben"
        ),
        "settings": {
            "chat_backend": "ananta-worker",
            "chat_use_codecompass": False,
            "chat_retrieval_profile": "none",
            "chat_code_questions_repo_first": False,
            "chat_architecture_analysis_mode": False,
            "chat_answer_chars": 3000,
            "chat_rag_top_k": 5,
            "chat_include_local_project": False,
        },
    },
    {
        # Read-only log session for the visual snake (ananta-visual).
        # The user cannot post to this session directly — the backend writes
        # [ui-tick] system messages and the proactive guide's reply into it
        # so the user can review what the visual snake observed and answered.
        "id": "ananta-visual",
        "name": "Visual Snake Log",
        "icon": "🐍",
        "group": "Konfiguration",
        "system_prompt": (
            "Read-only Log-Session für die visuelle AI-Snake.\n"
            "Eingehend: [ui-tick] System-Messages mit kompaktem UI-Snapshot der aktuellen App-Ansicht.\n"
            "Ausgehend: Proaktive Antworten der Guide-Snake (max. 1-2 kurze deutsche Sätze + optional __GUIDE__ Steps).\n"
            "Du kannst in dieser Session nicht direkt chatten — sie wird ausschließlich vom Backend befüllt."
        ),
        "settings": {
            "chat_backend": "ananta-worker",
            "chat_use_codecompass": False,
            "chat_retrieval_profile": "none",
            "chat_code_questions_repo_first": False,
            "chat_architecture_analysis_mode": False,
            "chat_answer_chars": 1000,
            "chat_rag_top_k": 0,
            "chat_include_local_project": False,
            "chat_read_only": True,
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

    # Build index of built-in default sessions by ID for migration
    _default_by_id = {str(d.get("id") or ""): d for d in DEFAULT_SESSIONS if d.get("id")}

    # Re-sync settings of existing built-in sessions when DEFAULT_SESSIONS changes.
    # User-customised sessions (not in DEFAULT_SESSIONS) are left untouched.
    #
    # For built-in sessions, we want the canonical defaults to be present,
    # but the user's settings_delta overrides must win on every key the
    # user actually touched.  Without this, any user-set value gets blown
    # away on the next get_sessions() call.
    for s in sessions:
        if not isinstance(s, dict):
            continue
        sid = str(s.get("id") or "")
        if sid in _default_by_id:
            import copy as _copy
            user_delta = dict(s.get("settings_delta") or {})
            # Canonical = full defaults, with the built-in's known delta on top.
            canonical_settings = _copy.deepcopy(_DEFAULT_SESSION_SETTINGS)
            for k, v in (dict(_default_by_id[sid].get("settings") or {})).items():
                canonical_settings[k] = v
            # Re-apply user overrides so the user's value wins.
            for k, v in user_delta.items():
                canonical_settings[k] = v
            current_settings = dict(s.get("settings") or {})
            if canonical_settings != current_settings:
                s["settings"] = canonical_settings
                _ensure_settings_delta(s)
            # Also sync system_prompt for built-in sessions so prompt changes take effect
            canonical_prompt = str(_default_by_id[sid].get("system_prompt") or "")
            if canonical_prompt and s.get("system_prompt") != canonical_prompt:
                s["system_prompt"] = canonical_prompt

    if bool(chat.get("_append_missing_default_sessions")) and not bool(chat.get("_preserve_session_list")):
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
    raw_sessions = chat.get("ai_sessions")
    sessions = raw_sessions if isinstance(raw_sessions, list) and raw_sessions else get_sessions(chat)
    for s in sessions:
        if isinstance(s, dict) and str(s.get("id") or "") == str(session_id):
            return s
    return None


def get_active_session(chat: dict[str, Any]) -> dict[str, Any] | None:
    raw_sessions = chat.get("ai_sessions")
    sessions = raw_sessions if isinstance(raw_sessions, list) and raw_sessions else get_sessions(chat)
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
    raw_sessions = chat.get("ai_sessions")
    sessions = raw_sessions if isinstance(raw_sessions, list) and raw_sessions else get_sessions(chat)
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
    raw_sessions = chat.get("ai_sessions")
    if isinstance(raw_sessions, list) and raw_sessions:
        target_session = next(
            (
                session for session in raw_sessions
                if isinstance(session, dict) and str(session.get("id") or "") == target_id
            ),
            None,
        )
    else:
        target_session = get_session(chat, target_id)
    if target_session is None:
        return False
    chat["active_session_id"] = target_id
    chat["active_channel"] = f"ai:{target_id}"
    # Make sure the channel exists — important when callers switch to
    # a session that was added after the initial ensure_session_channels
    # call (e.g. a freshly added custom session).
    previous_preserve = chat.get("_preserve_session_list", None)
    had_preserve = "_preserve_session_list" in chat
    chat["_preserve_session_list"] = True
    try:
        ensure_session_channels(chat)
    finally:
        if had_preserve:
            chat["_preserve_session_list"] = previous_preserve
        else:
            chat.pop("_preserve_session_list", None)
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
    raw_sessions = chat.get("ai_sessions")
    sessions = raw_sessions if isinstance(raw_sessions, list) and raw_sessions else get_sessions(chat)
    for session in sessions:
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
