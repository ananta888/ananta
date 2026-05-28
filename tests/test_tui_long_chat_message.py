from __future__ import annotations

import re

from client_surfaces.operator_tui.chat_long_message import (
    LONG_CHAT_MESSAGE_THRESHOLD,
    compact_chat_message_text,
    configure_middle_view_for_message,
    latest_long_message_for_channel,
    markdown_for_message,
    should_use_middle_view_for_message,
)
from client_surfaces.operator_tui.chat_state import append_message, default_chat_state, make_message
from client_surfaces.operator_tui.models import OperatorState
from client_surfaces.operator_tui.renderer import render_operator_shell


_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b.")


def test_long_chat_message_is_compacted_with_shortcut_hint() -> None:
    text = "x" * (LONG_CHAT_MESSAGE_THRESHOLD + 25)

    compacted = compact_chat_message_text(text, shortcut_display="Ctrl+Space")

    assert compacted.startswith("x" * LONG_CHAT_MESSAGE_THRESHOLD)
    assert "Ctrl+Space" in compacted
    assert "Markdown/Mermaid" in compacted
    assert "mittleren" in compacted


def test_latest_long_message_for_channel_returns_only_the_active_long_message() -> None:
    short_msg = {"id": "short", "text": "kurz"}
    long_msg = {"id": "long", "sender_id": "s-ai", "sender_kind": "ai", "text": "m" * 140}
    channel = {"messages": [short_msg, long_msg]}

    assert latest_long_message_for_channel(channel) == long_msg
    assert markdown_for_message(long_msg).endswith("m" * 140)


def test_user_messages_do_not_auto_move_to_middle_view() -> None:
    msg = {"id": "user-long", "sender_id": "s1", "sender_kind": "user", "text": "u" * 140}

    assert should_use_middle_view_for_message(msg) is False
    assert latest_long_message_for_channel({"messages": [msg]}) is None


def test_configure_middle_view_for_streaming_answer() -> None:
    game: dict[str, object] = {}
    msg = {"id": "streaming", "sender_id": "s-ai", "sender_kind": "ai", "text": "a" * 140}

    changed = configure_middle_view_for_message(game, msg, channel_id="ai:tutor", streaming=True)

    assert changed is True
    assert game["visual_viewport_enabled"] is True
    assert game["visual_viewport_active_view_request"] == "markdown_mermaid_document"
    assert game["visual_viewport_force_render"] is True
    assert game["markdown_auto_follow"] is True
    assert "Antwortstream wird hier" in str(game["chat_long_message_markdown"])


def test_chat_renderer_shows_hint_after_100_chars() -> None:
    chat = default_chat_state()
    append_message(
        chat,
        make_message(
            channel_id="room:main",
            channel_type="room",
            sender_id="s-ai",
            sender_kind="ai",
            text="Antwort " + ("lang " * 40),
            delivery_state="received",
        ),
    )
    state = OperatorState(
        endpoint="http://localhost:5000",
        header_logo_game={"chat_panel_open": True, "chat_state": chat},
    )

    rendered = _ANSI_RE.sub("", render_operator_shell(state, width=120, height=32))

    assert "Ctrl+Space" in rendered
    assert "mittleren" in rendered
