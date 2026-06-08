"""Tests for the chat-sessions model: CRUD, settings routing, message clearing,
TUI session-management commands."""
from __future__ import annotations

import sys

import pytest


# ── chat_state helpers ────────────────────────────────────────────────────


def test_make_session_uses_defaults_when_no_settings_passed() -> None:
    """A session with no settings should still have the full default
    settings dict (so downstream `chat_backend`, `chat_use_codecompass`
    etc. lookups work without key-existence checks)."""
    from client_surfaces.operator_tui.chat_state import make_session

    s = make_session(session_id="x", name="X")
    assert s["id"] == "x"
    assert s["name"] == "X"
    assert isinstance(s["settings"], dict)
    # Defaults must include the keys that get_effective_chat_settings cares about
    assert "chat_backend" in s["settings"]
    assert "chat_use_codecompass" in s["settings"]
    assert "chat_source_pack_id" in s["settings"]


def test_make_session_merges_settings_over_defaults() -> None:
    """Settings overrides must merge on top of the defaults, not replace
    them — otherwise the session would lose `chat_max_context_chars` etc."""
    from client_surfaces.operator_tui.chat_state import make_session

    s = make_session(
        session_id="x", name="X",
        settings={"chat_backend": "ananta-worker"},
    )
    assert s["settings"]["chat_backend"] == "ananta-worker"
    # Defaults that weren't overridden are still there
    assert "chat_use_codecompass" in s["settings"]
    assert "chat_context_chars" in s["settings"]


def test_default_sessions_are_isolated() -> None:
    """default_sessions() must return a fresh list each time, with
    isolated settings dicts. Mutating one session's settings must
    not leak into another call."""
    from client_surfaces.operator_tui.chat_state import default_sessions

    s1 = default_sessions()
    s2 = default_sessions()
    assert s1 is not s2
    assert s1[0]["settings"] is not s2[0]["settings"]
    s1[0]["settings"]["chat_backend"] = "MUTATED"
    assert s2[0]["settings"]["chat_backend"] != "MUTATED"


def test_get_effective_chat_settings_uses_active_session_overrides() -> None:
    """The merged settings must come from the active session's overrides
    on top of the game-level defaults. A non-active session's overrides
    must NOT leak into the effective settings."""
    from client_surfaces.operator_tui.chat_state import (
        get_effective_chat_settings,
    )

    game = {
        "chat_backend": "lmstudio",  # legacy game-level default
        "chat_use_codecompass": False,
    }
    chat = {
        "ai_sessions": [
            {"id": "code-help", "settings": {"chat_backend": "ananta-worker", "chat_use_codecompass": True}},
            {"id": "writing-coach", "settings": {"chat_backend": "lmstudio"}},
        ],
        "active_session_id": "code-help",
    }
    eff = get_effective_chat_settings(chat, game)
    assert eff["chat_backend"] == "ananta-worker"  # session override
    assert eff["chat_use_codecompass"] is True      # session override
    # writing-coach override (different backend) must NOT leak
    assert "writing-coach" not in str(eff)


def test_get_effective_chat_settings_session_prompt_beats_settings() -> None:
    """The session's own `system_prompt` field is exposed as
    `chat_system_prompt` in the effective settings — preferred over
    any value in `settings.chat_system_prompt`."""
    from client_surfaces.operator_tui.chat_state import (
        get_effective_chat_settings,
    )

    chat = {
        "ai_sessions": [
            {
                "id": "x", "name": "X",
                "system_prompt": "You are X.",
                "settings": {"chat_system_prompt": "ignore this"},
            },
        ],
        "active_session_id": "x",
    }
    eff = get_effective_chat_settings(chat, {})
    assert eff["chat_system_prompt"] == "You are X."


def test_get_effective_chat_settings_empty_session_value_falls_through() -> None:
    """If a session has an override value of "" or None, the game-level
    default should be kept — so users can clear a session override by
    emptying it in the settings UI."""
    from client_surfaces.operator_tui.chat_state import (
        get_effective_chat_settings,
    )

    game = {"chat_backend": "lmstudio", "chat_use_codecompass": True}
    chat = {
        "ai_sessions": [
            {"id": "x", "settings": {"chat_backend": ""}},  # empty
        ],
        "active_session_id": "x",
    }
    eff = get_effective_chat_settings(chat, game)
    assert eff["chat_backend"] == "lmstudio"  # fell through
    assert eff["chat_use_codecompass"] is True  # session didn't override


def test_clear_session_messages_only_clears_target_channel() -> None:
    """clear_session_messages(chat, sid) must zero out ONLY the
    session's channel messages, leaving room, notes, system, and
    other sessions' channels untouched."""
    from client_surfaces.operator_tui.chat_state import (
        clear_session_messages, get_chat_state, default_sessions,
    )

    chat = get_chat_state({})
    sessions = chat["ai_sessions"]
    # Put messages in two session channels
    sessions[0]["messages"] = [{"text": "keep me"}]
    chat["channels"][f"ai:{sessions[0]['id']}"]["messages"] = [{"text": "delete me"}]
    chat["channels"]["room:main"]["messages"] = [{"text": "keep me room"}]
    chat["channels"]["notes:self"]["messages"] = [{"text": "keep me notes"}]

    # Clear only the first session's channel
    target_id = sessions[0]["id"]
    assert clear_session_messages(chat, target_id) is True
    assert chat["channels"][f"ai:{target_id}"]["messages"] == []
    # Other channels untouched
    assert chat["channels"]["room:main"]["messages"] == [{"text": "keep me room"}]
    assert chat["channels"]["notes:self"]["messages"] == [{"text": "keep me notes"}]


def test_clear_session_messages_unknown_id_returns_false() -> None:
    """clear_session_messages(chat, 'nonexistent') must return False and
    not raise. The chat state must be unchanged."""
    from client_surfaces.operator_tui.chat_state import (
        clear_session_messages, get_chat_state,
    )

    chat = get_chat_state({})
    before = dict(chat["channels"])
    assert clear_session_messages(chat, "does-not-exist") is False
    assert chat["channels"] == before


def test_clear_all_session_messages_clears_every_session_not_room() -> None:
    """clear_all_session_messages zeroes every session's channel but
    does NOT touch room, notes, system — those are non-session
    channels and have their own meaning."""
    from client_surfaces.operator_tui.chat_state import (
        clear_all_session_messages, get_chat_state,
    )

    chat = get_chat_state({})
    for ch in chat["channels"].values():
        ch["messages"] = [{"text": "msg"}]
    chat["channels"]["room:main"]["messages"] = [{"text": "room msg"}]
    n_cleared = clear_all_session_messages(chat)
    assert n_cleared == len(chat["ai_sessions"])
    # Sessions cleared
    for sid in [s["id"] for s in chat["ai_sessions"]]:
        assert chat["channels"][f"ai:{sid}"]["messages"] == []
    # Non-session channels kept
    assert chat["channels"]["room:main"]["messages"] == [{"text": "room msg"}]


def test_add_and_delete_session_round_trip() -> None:
    """add_session + delete_session must be inverse operations. The
    active_session_id must be repaired if the deleted session was
    active."""
    from client_surfaces.operator_tui.chat_state import (
        add_session, delete_session, get_active_session, get_chat_state,
        get_session, make_session,
    )

    chat = get_chat_state({})
    initial_n = len(chat["ai_sessions"])
    new = make_session(session_id="ephemeral", name="Ephemeral", settings={})
    add_session(chat, new)
    assert get_session(chat, "ephemeral") is not None
    assert len(chat["ai_sessions"]) == initial_n + 1

    # Switch to it so we test the auto-repair of active_session_id
    chat["active_session_id"] = "ephemeral"
    deleted = delete_session(chat, "ephemeral")
    assert deleted is True
    assert get_session(chat, "ephemeral") is None
    # Active session must have been repaired to a remaining session
    active = get_active_session(chat)
    assert active is not None
    assert active["id"] != "ephemeral"


def test_delete_last_session_is_blocked() -> None:
    """Users must not be able to delete their only remaining session —
    otherwise they'd have no chat at all. delete_session must return
    False and the session list must be unchanged."""
    from client_surfaces.operator_tui.chat_state import (
        delete_session, get_chat_state, set_active_session,
    )

    chat = get_chat_state({})
    # All but one session removed
    first = chat["ai_sessions"][0]
    chat["ai_sessions"] = [first]
    set_active_session(chat, first["id"])
    assert delete_session(chat, first["id"]) is False
    assert len(chat["ai_sessions"]) == 1
    assert chat["ai_sessions"][0]["id"] == first["id"]


def test_legacy_chat_state_migrates_to_sessions() -> None:
    """A `chat_state` dict with no `ai_sessions` key (the pre-sessions
    shape) must be upgraded in-place by get_chat_state — with the
    existing `ai:tutor` channel preserved as a session so history is
    not lost. Default channels (room, notes, system) must also be
    backfilled if missing."""
    from client_surfaces.operator_tui.chat_state import get_chat_state

    legacy_game = {
        "local_snake_id": "s-legacy",
        "chat_state": {
            "local_snake_id": "s-legacy",
            "active_channel": "ai:tutor",
            "channels": {
                "ai:tutor": {
                    "id": "ai:tutor",
                    "messages": [{"text": "old conversation"}],
                },
            },
        },
    }
    chat = get_chat_state(legacy_game)
    # Sessions present and legacy channel preserved
    assert "ai_sessions" in chat
    assert len(chat["ai_sessions"]) >= 1
    # The legacy ai:tutor channel with its messages must still be there
    assert chat["channels"]["ai:tutor"]["messages"] == [{"text": "old conversation"}]
    # Default non-session channels must be present after migration
    for default_ch in ("room:main", "notes:self"):
        assert default_ch in chat["channels"], f"missing {default_ch}"


# ── TUI session-management commands ────────────────────────────────────────


def _build_tui() -> object:
    """Build a fresh InteractiveOperatorTui with chat-focus active."""
    from client_surfaces.operator_tui.interactive import InteractiveOperatorTui
    from client_surfaces.operator_tui.models import FocusPane, OperatorState

    state = OperatorState(
        endpoint="http://localhost:5000",
        focus=FocusPane.HEADER,
        header_logo_game={"active": True, "alive": True, "ui_steering": True},
    )
    tui = InteractiveOperatorTui(state)
    tui._chat_focus_enter()
    return tui


def _type(tui: object, text: str) -> None:
    """Type `text` into the chat input buffer, character by character,
    the way the real key-bindings do. This is what the existing TUI
    tests do — direct attribute assignment doesn't go through the
    input pipeline that _chat_send_message reads from."""
    for ch in text:
        tui._chat_append(ch)


def test_session_command_lists_all_sessions_in_active_channel() -> None:
    """`/session` (no args) must post a system message into the active
    channel listing every session, with the active one marked."""
    tui = _build_tui()
    _type(tui, "/session")
    tui._chat_send_message()
    chat = tui.state.header_logo_game["chat_state"]
    active_ch_id = chat["active_channel"]
    msgs = chat["channels"][active_ch_id]["messages"]
    assert any("[TUI] Sessions:" in m["text"] for m in msgs), msgs
    # Active session marker
    sessions = chat["ai_sessions"]
    active_id = chat["active_session_id"]
    assert active_id in [s["id"] for s in sessions]


def test_session_command_new_creates_and_switches() -> None:
    """`/session new <name>` must create a new session, switch to it,
    and update the active channel to the new session's channel."""
    tui = _build_tui()
    _type(tui, "/session new test-session")
    tui._chat_send_message()
    chat = tui.state.header_logo_game["chat_state"]
    assert any(s["id"] == "test-session" for s in chat["ai_sessions"])
    assert chat["active_session_id"] == "test-session"
    assert chat["active_channel"] == "ai:test-session"


def test_session_command_delete_removes_and_auto_repairs_active() -> None:
    """Deleting the active session must auto-repair the active
    session pointer to another remaining session."""
    tui = _build_tui()
    chat = tui.state.header_logo_game["chat_state"]
    # First create an extra session so deletion has something to fall back to
    _type(tui, "/session new victim")
    tui._chat_send_message()
    new_id = chat["active_session_id"]
    assert new_id is not None and "victim" in new_id

    _type(tui, f"/session delete {new_id}")
    tui._chat_send_message()
    assert not any(s["id"] == new_id for s in chat["ai_sessions"])
    # Active session must NOT be the deleted one
    assert chat["active_session_id"] != new_id


def test_session_command_delete_last_is_blocked() -> None:
    """Deleting the last remaining session must be refused with a
    helpful status message."""
    tui = _build_tui()
    chat = tui.state.header_logo_game["chat_state"]
    # Trim down to one session
    chat["ai_sessions"] = [chat["ai_sessions"][0]]
    only_id = chat["ai_sessions"][0]["id"]
    chat["active_session_id"] = only_id
    _type(tui, f"/session delete {only_id}")
    tui._chat_send_message()
    assert len(chat["ai_sessions"]) == 1
    assert "letzter" in tui.state.status_message.lower() or "nicht löschbar" in tui.state.status_message.lower()


def test_session_command_rename_updates_name() -> None:
    """`/session rename <id> <new name>` must change the session's
    `name` field and refresh the channel's display_name."""
    tui = _build_tui()
    chat = tui.state.header_logo_game["chat_state"]
    target_id = chat["ai_sessions"][0]["id"]
    _type(tui, f"/session rename {target_id} Mein-Neuer-Name")
    tui._chat_send_message()
    target = next(s for s in chat["ai_sessions"] if s["id"] == target_id)
    assert target["name"] == "Mein-Neuer-Name"


def test_session_command_switch_changes_active_channel() -> None:
    """`/session <id>` must switch to the named session and update
    the active channel so the user immediately sees that session's
    history."""
    tui = _build_tui()
    chat = tui.state.header_logo_game["chat_state"]
    # Add a second session so we can switch to a non-active one
    _type(tui, "/session new other")
    tui._chat_send_message()
    # The new session is now active; capture its (possibly disambiguated)
    # id so we can switch back to the original reliably.
    new_id = chat["active_session_id"]
    assert new_id is not None and new_id != "code-help"
    # Now switch back to the original
    original = [s for s in chat["ai_sessions"] if s["id"] != new_id][0]
    _type(tui, f"/session {original['id']}")
    tui._chat_send_message()
    assert chat["active_session_id"] == original["id"]
    assert chat["active_channel"] == f"ai:{original['id']}"


def test_clear_command_clears_active_session_only() -> None:
    """`/clear` (no args) must clear the active session's history but
    leave other sessions' histories intact."""
    tui = _build_tui()
    chat = tui.state.header_logo_game["chat_state"]
    # Add a second session but stay in the original so the active
    # channel is the original session's channel.
    _type(tui, "/session new other")
    tui._chat_send_message()
    other_id = chat["active_session_id"]
    assert other_id is not None and "other" in other_id
    other_ch = f"ai:{other_id}"
    _type(tui, f"/session {chat['ai_sessions'][0]['id']}")  # back to first session
    tui._chat_send_message()
    # Put messages in both
    chat["channels"][chat["active_channel"]]["messages"] = [{"text": "in active"}]
    chat["channels"][other_ch]["messages"] = [{"text": "in other"}]

    _type(tui, "/clear")
    tui._chat_send_message()
    # User message wiped; only the system status message may remain.
    active_msgs = chat["channels"][chat["active_channel"]]["messages"]
    assert not any(m.get("text") == "in active" for m in active_msgs)
    # Other session's user message must still be there
    other_msgs = chat["channels"][other_ch]["messages"]
    assert any(m.get("text") == "in other" for m in other_msgs)


def test_clear_command_with_id_clears_target_session() -> None:
    """`/clear <id>` must clear the named session's messages — even
    if it's not the active one."""
    tui = _build_tui()
    chat = tui.state.header_logo_game["chat_state"]
    _type(tui, "/session new other")
    tui._chat_send_message()
    other_id = chat["active_session_id"]
    assert other_id is not None and "other" in other_id
    other_ch = f"ai:{other_id}"
    # Switch back to the first session so "active" and "other" are
    # distinct channels. Without this both `in active` and `in other`
    # would end up in the same channel.
    _type(tui, f"/session {chat['ai_sessions'][0]['id']}")
    tui._chat_send_message()
    chat["channels"][chat["active_channel"]]["messages"] = [{"text": "in active"}]
    chat["channels"][other_ch]["messages"] = [{"text": "in other"}]

    _type(tui, f"/clear {other_id}")
    tui._chat_send_message()
    # The clear-status message is the only remaining entry; pre-existing
    # user messages must have been wiped.
    msgs = chat["channels"][other_ch]["messages"]
    assert all(m.get("sender_kind") == "system" for m in msgs)
    assert not any(m.get("text") == "in other" for m in msgs)
    # The active channel must still contain the original user message
    # plus the system status reply from the clear command.
    active_msgs = chat["channels"][chat["active_channel"]]["messages"]
    assert any(m.get("text") == "in active" for m in active_msgs)
    assert any("Verlauf von Session" in m.get("text", "") for m in active_msgs)


def test_clear_command_all_clears_every_session() -> None:
    """`/clear all` must clear every session's messages."""
    tui = _build_tui()
    chat = tui.state.header_logo_game["chat_state"]
    for ch in chat["channels"].values():
        ch["messages"] = [{"text": "msg"}]
    _type(tui, "/clear all")
    tui._chat_send_message()
    for s in chat["ai_sessions"]:
        msgs = chat["channels"][f"ai:{s['id']}"]["messages"]
        # All user messages gone; only system status may remain
        assert not any(m.get("text") == "msg" for m in msgs)
    # Non-session channels must be untouched
    assert chat["channels"]["room:main"]["messages"] == [{"text": "msg"}]
