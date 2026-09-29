from __future__ import annotations

import re

from client_surfaces.operator_tui.chat_long_message import (
    LONG_CHAT_MESSAGE_THRESHOLD,
    compact_chat_message_text,
    configure_middle_view_for_message,
    configure_middle_view_for_history_entry,
    latest_long_message_for_channel,
    long_message_history_rows,
    markdown_for_message,
    refresh_rendered_view,
    should_use_middle_view_for_message,
    toggle_render_mode,
)
from client_surfaces.operator_tui.chat_state import append_message, default_chat_state, make_message
from client_surfaces.operator_tui.models import FocusPane, OperatorState
from client_surfaces.operator_tui.renderer import render_operator_shell


_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b.")


def test_long_chat_message_is_compacted_with_shortcut_hint() -> None:
    text = "x" * (LONG_CHAT_MESSAGE_THRESHOLD + 25)

    compacted = compact_chat_message_text(text, shortcut_display="Ctrl+Space")

    assert compacted.startswith("x" * LONG_CHAT_MESSAGE_THRESHOLD)
    assert "Ctrl+Space" in compacted
    assert "mittleren" in compacted
    assert "vollständig" in compacted


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
    assert game["markdown_stream_plain"] is True
    assert game["chat_long_message_plain_text"] == "a" * 140
    assert "Antwortstream wird hier" in str(game["chat_long_message_markdown"])


def test_configure_middle_view_starts_as_cached_original_output() -> None:
    game: dict[str, object] = {}
    msg = {"id": "answer-1", "sender_id": "s-ai", "sender_kind": "ai", "text": "a" * 140}

    changed = configure_middle_view_for_message(game, msg, channel_id="ai:tutor", streaming=False)

    assert changed is True
    assert game["markdown_stream_plain"] is True
    assert game["markdown_mermaid_render_requested"] is False
    assert game["markdown_mermaid_config"]["mermaid_mode"] == "disabled"  # type: ignore[index]
    assert game["chat_long_message_plain_text"] == "a" * 140
    rows = long_message_history_rows(game)
    assert len(rows) == 1
    assert rows[0]["id"] == "answer-1"
    assert rows[0]["text"] == "a" * 140


def test_toggle_render_mode_preserves_original_output() -> None:
    game: dict[str, object] = {}
    msg = {"id": "answer-1", "sender_id": "s-ai", "sender_kind": "ai", "text": "a" * 140}
    configure_middle_view_for_message(game, msg, channel_id="ai:tutor", streaming=False)

    rendered_mode = toggle_render_mode(game)

    assert rendered_mode == "rendered"
    assert game["markdown_stream_plain"] is False
    assert game["markdown_mermaid_render_requested"] is True
    assert game["markdown_mermaid_config"]["mermaid_mode"] == "auto"  # type: ignore[index]
    assert game["chat_long_message_plain_text"] == "a" * 140

    plain_mode = toggle_render_mode(game)

    assert plain_mode == "plain"
    assert game["markdown_stream_plain"] is True
    assert game["markdown_mermaid_render_requested"] is False
    assert game["markdown_mermaid_config"]["mermaid_mode"] == "disabled"  # type: ignore[index]
    assert game["chat_long_message_plain_text"] == "a" * 140


def test_refresh_rendered_view_keeps_cached_original_output() -> None:
    game: dict[str, object] = {"visual_viewport_frame_lines": ["stale"]}
    msg = {"id": "answer-1", "sender_id": "s-ai", "sender_kind": "ai", "text": "a" * 140}
    configure_middle_view_for_message(game, msg, channel_id="ai:tutor", streaming=False)

    refresh_rendered_view(game)

    assert game["markdown_stream_plain"] is False
    assert game["markdown_mermaid_render_requested"] is True
    assert game["markdown_mermaid_config"]["mermaid_mode"] == "auto"  # type: ignore[index]
    assert game["visual_viewport_frame_lines"] == []
    assert game["chat_long_message_plain_text"] == "a" * 140


def test_configure_middle_view_for_history_entry_shows_plain_original() -> None:
    game: dict[str, object] = {}
    entry = {
        "id": "answer-1",
        "channel_id": "ai:tutor",
        "sender_id": "s-ai",
        "sender_kind": "ai",
        "text": "a" * 140,
        "markdown": "# Chat-Nachricht\n\n" + "a" * 140,
    }

    changed = configure_middle_view_for_history_entry(game, entry)

    assert changed is True
    assert game["chat_long_message_plain_text"] == "a" * 140
    assert game["markdown_stream_plain"] is True
    assert game["markdown_mermaid_render_requested"] is False


def test_chat_renderer_shows_hint_after_100_chars() -> None:
    chat = default_chat_state()
    active_channel = str(chat["active_channel"])
    append_message(
        chat,
        make_message(
            channel_id=active_channel,
            channel_type="ai",
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
    assert "fortgesetzt" in rendered


def test_navigation_renders_long_message_history_tree() -> None:
    game = {
        "chat_long_message_history": [
            {
                "id": "answer-1",
                "channel_id": "ai:tutor",
                "sender_kind": "ai",
                "text": "Antwort mit Mermaid und weiterem Kontext",
                "preview": "Antwort mit Mermaid und weiterem Kontext",
                "created_at": 10.0,
            }
        ]
    }
    state = OperatorState(
        endpoint="http://localhost:5000",
        focus=FocusPane.NAVIGATION,
        selected_index=6,
        header_logo_game=game,
    )

    rendered = _ANSI_RE.sub("", render_operator_shell(state, width=120, height=32))

    assert "Chat History" in rendered
    assert "ai:tutor" in rendered
    assert "[ai] Antwort" in rendered
