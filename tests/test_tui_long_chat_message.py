from __future__ import annotations

import re

from client_surfaces.operator_tui.chat_long_message import (
    LONG_CHAT_MESSAGE_THRESHOLD,
    compact_chat_message_text,
    latest_long_message_for_channel,
    markdown_for_message,
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
    assert len(compacted) < len(text) + 80


def test_latest_long_message_for_channel_returns_only_the_active_long_message() -> None:
    short_msg = {"id": "short", "text": "kurz"}
    long_msg = {"id": "long", "sender_id": "s-ai", "sender_kind": "ai", "text": "m" * 140}
    channel = {"messages": [short_msg, long_msg]}

    assert latest_long_message_for_channel(channel) == long_msg
    assert markdown_for_message(long_msg).endswith("m" * 140)


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
    assert "Rest im mittleren" in rendered
