"""T07.01: Unit-Tests für Chat State – Channel, Message, Unread, Notes-Invarianten."""
from __future__ import annotations

import pytest

from client_surfaces.operator_tui.chat_state import (
    ChannelType,
    DeliveryState,
    Visibility,
    append_message,
    default_channels,
    default_chat_state,
    get_active_channel,
    get_channel,
    get_chat_state,
    make_channel,
    make_message,
    sanitize_text,
    switch_channel,
    unread_total,
    add_direct_channel,
)


# ── Channel tests ─────────────────────────────────────────────────────────────


def test_default_channels_contain_required_types():
    channels = default_channels()
    assert "room:main" in channels
    assert "ai:tutor" in channels
    assert "notes:self" in channels
    assert "system" in channels


def test_channel_type_values():
    assert ChannelType.ROOM == "room"
    assert ChannelType.AI == "ai"
    assert ChannelType.NOTES == "notes"
    assert ChannelType.DIRECT == "direct"
    assert ChannelType.SYSTEM == "system"


def test_make_channel_room_defaults():
    ch = make_channel("room:main", ChannelType.ROOM, "#room")
    assert ch["channel_type"] == "room"
    assert ch["visibility"] == Visibility.ROOM
    assert ch["persistence_policy"] == "hub"
    assert ch["unread"] == 0


def test_make_channel_notes_defaults():
    ch = make_channel("notes:self", ChannelType.NOTES, "notes")
    assert ch["visibility"] == Visibility.LOCAL_ONLY
    assert ch["persistence_policy"] == "local_only"


def test_make_channel_ai_defaults():
    ch = make_channel("ai:tutor", ChannelType.AI, "AI")
    assert ch["visibility"] == Visibility.AI_CONTEXT
    assert ch["persistence_policy"] == "local"


def test_default_chat_state_has_correct_active_channel():
    cs = default_chat_state("s1")
    # Seit den benannten Chat-Sessions ist der aktive Kanal die erste Session.
    assert cs["active_channel"] == f"ai:{cs['active_session_id']}"
    assert cs["active_channel"] in cs["channels"]
    assert cs["local_snake_id"] == "s1"
    assert not cs["chat_focus"]


def test_switch_channel_updates_active():
    cs = default_chat_state("s1")
    result = switch_channel(cs, "notes:self")
    assert result is True
    assert cs["active_channel"] == "notes:self"


def test_switch_channel_resets_unread():
    cs = default_chat_state("s1")
    cs["channels"]["notes:self"]["unread"] = 5
    switch_channel(cs, "notes:self")
    assert cs["channels"]["notes:self"]["unread"] == 0


def test_switch_channel_unknown_returns_false():
    cs = default_chat_state("s1")
    before = cs["active_channel"]
    result = switch_channel(cs, "nonexistent:channel")
    assert result is False
    assert cs["active_channel"] == before


def test_get_active_channel_returns_correct():
    cs = default_chat_state("s1")
    ch = get_active_channel(cs)
    assert ch is not None
    assert ch["id"] == cs["active_channel"]


def test_add_direct_channel_creates_new():
    cs = default_chat_state("s1")
    ch_id = add_direct_channel(cs, "s-abc", "Alice")
    assert ch_id == "direct:s-abc"
    assert "direct:s-abc" in cs["channels"]


def test_add_direct_channel_idempotent():
    cs = default_chat_state("s1")
    id1 = add_direct_channel(cs, "s-abc", "Alice")
    id2 = add_direct_channel(cs, "s-abc", "Alice")
    assert id1 == id2
    # Should not duplicate
    count = sum(1 for k in cs["channels"] if k == "direct:s-abc")
    assert count == 1


# ── Message tests ─────────────────────────────────────────────────────────────


def test_make_message_required_fields():
    msg = make_message(
        channel_id="room:main", channel_type="room",
        sender_id="s1", text="hello"
    )
    assert msg["channel_id"] == "room:main"
    assert msg["channel_type"] == "room"
    assert msg["sender_id"] == "s1"
    assert msg["text"] == "hello"
    assert "id" in msg
    assert "created_at" in msg
    assert msg["visibility"] == Visibility.ROOM
    assert msg["delivery_state"] == DeliveryState.DRAFT


def test_make_message_notes_visibility():
    msg = make_message(
        channel_id="notes:self", channel_type="notes",
        sender_id="s1", text="private note"
    )
    assert msg["visibility"] == Visibility.LOCAL_ONLY


def test_make_message_ai_visibility():
    msg = make_message(
        channel_id="ai:tutor", channel_type="ai",
        sender_id="s1", text="question"
    )
    assert msg["visibility"] == Visibility.AI_CONTEXT


def test_make_message_text_clamped_at_500():
    long_text = "x" * 600
    msg = make_message(channel_id="room:main", channel_type="room", sender_id="s1", text=long_text)
    assert len(msg["text"]) == 500


def test_append_message_adds_to_channel():
    cs = default_chat_state("s1")
    msg = make_message(channel_id="room:main", channel_type="room", sender_id="s1", text="hi")
    msg["delivery_state"] = "sent"
    append_message(cs, msg)
    assert len(cs["channels"]["room:main"]["messages"]) == 1


def test_append_message_deduplicates_by_id():
    cs = default_chat_state("s1")
    msg = make_message(channel_id="room:main", channel_type="room", sender_id="s1", text="hi")
    append_message(cs, msg)
    append_message(cs, msg)  # same id
    assert len(cs["channels"]["room:main"]["messages"]) == 1


def test_append_message_increments_unread_for_inactive_channel():
    cs = default_chat_state("s1")
    # active is room:main
    msg = make_message(channel_id="notes:self", channel_type="notes", sender_id="s1", text="note")
    append_message(cs, msg)
    assert cs["channels"]["notes:self"]["unread"] == 1


def test_append_message_no_unread_for_active_channel():
    cs = default_chat_state("s1")
    active = cs["active_channel"]
    msg = make_message(channel_id=active, channel_type="ai", sender_id="s1", text="hi")
    append_message(cs, msg)
    assert cs["channels"][active]["unread"] == 0


def test_append_message_unknown_channel_does_nothing():
    cs = default_chat_state("s1")
    msg = make_message(channel_id="unknown:ch", channel_type="room", sender_id="s1", text="oops")
    before = len(cs["channels"])
    append_message(cs, msg)
    assert len(cs["channels"]) == before  # no new channel created


# ── Notes local-only invariant ────────────────────────────────────────────────


def test_notes_channel_local_only_visibility():
    ch = make_channel("notes:self", ChannelType.NOTES, "notes")
    assert ch["visibility"] == "local_only"
    assert ch["persistence_policy"] == "local_only"


def test_notes_message_always_local_only():
    msg = make_message(
        channel_id="notes:self", channel_type="notes",
        sender_id="s1", text="private"
    )
    assert msg["visibility"] == "local_only"


def test_notes_participants_only_self():
    ch = make_channel("notes:self", ChannelType.NOTES, "notes", participants=["self"])
    assert ch["participants"] == ["self"]


# ── Unread total ─────────────────────────────────────────────────────────────


def test_unread_total_zero_initially():
    cs = default_chat_state("s1")
    assert unread_total(cs) == 0


def test_unread_total_sums_all_channels():
    cs = default_chat_state("s1")
    cs["channels"]["room:main"]["unread"] = 3
    cs["channels"]["notes:self"]["unread"] = 2
    assert unread_total(cs) == 5


# ── Sanitize text ─────────────────────────────────────────────────────────────


def test_sanitize_text_strips_ansi():
    raw = "\x1b[31mHello\x1b[0m"
    result = sanitize_text(raw)
    assert result == "Hello"


def test_sanitize_text_strips_control_chars():
    raw = "Hello\x00World\x07"
    result = sanitize_text(raw)
    assert "Hello" in result
    assert "\x00" not in result


def test_sanitize_text_clamps_length():
    result = sanitize_text("a" * 600)
    assert len(result) == 500


def test_sanitize_text_strips_whitespace():
    result = sanitize_text("  hello  ")
    assert result == "hello"


# ── get_chat_state fallback ───────────────────────────────────────────────────


def test_get_chat_state_creates_default_if_missing():
    game: dict = {"local_snake_id": "s-test"}
    cs = get_chat_state(game)
    assert "channels" in cs
    assert cs["local_snake_id"] == "s-test"


def test_get_chat_state_returns_existing():
    cs = default_chat_state("s1")
    game: dict = {"chat_state": cs}
    result = get_chat_state(game)
    assert result is cs
