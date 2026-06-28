"""Backward-compatible facade for the split operator-TUI chat state."""
from __future__ import annotations

import time
from typing import Any

# ── Re-exports from chat_models ───────────────────────────────────────────────
from client_surfaces.operator_tui.chat_models import (
    ChatState,
    ChannelType,
    Visibility,
    DeliveryState,
    SenderKind,
    SourceRef,
    ChatAnswerBlock,
    make_message,
    make_heuristic_message,
)

# ── Re-exports from chat_channel ──────────────────────────────────────────────
from client_surfaces.operator_tui.chat_channel import (
    make_channel,
    default_channels,
    get_channel,
    get_active_channel,
    switch_channel,
    add_direct_channel,
)

# ── Re-exports from chat_session ──────────────────────────────────────────────
from client_surfaces.operator_tui.chat_session import (
    _DEFAULT_SESSION_SETTINGS,
    PREDICTIVE_GUIDE_KEYS,
    PRESET_PREDICTIVE_QUIET,
    PRESET_PREDICTIVE_BALANCED,
    PRESET_PREDICTIVE_EAGER,
    PREDICTIVE_PRESETS,
    DEFAULT_SESSIONS,
    make_session,
    default_sessions,
    _ensure_settings_delta,
    get_sessions,
    get_session,
    get_active_session,
    active_session_id,
    active_session_channel_id,
    _SESSION_OVERRIDE_KEYS,
    get_effective_chat_settings,
    clear_session_messages,
    clear_all_session_messages,
    add_session,
    delete_session,
    set_active_session,
    update_session_settings,
    ensure_session_channels,
)

# ── Re-exports from chat_message ──────────────────────────────────────────────
from client_surfaces.operator_tui.chat_message import (
    append_message,
    sanitize_text,
    unread_total,
    maybe_add_prediction_comment,
    append_artifact_graph_explanation,
)


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


def set_chat_state(game: dict[str, Any], chat: dict[str, Any]) -> None:
    game["chat_state"] = chat
