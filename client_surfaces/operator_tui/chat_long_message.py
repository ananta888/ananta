from __future__ import annotations

from typing import Any

from client_surfaces.operator_tui.keybindings_config import display_for_action

LONG_CHAT_MESSAGE_THRESHOLD = 100


def is_long_chat_message(text: str, *, threshold: int = LONG_CHAT_MESSAGE_THRESHOLD) -> bool:
    return len(str(text or "")) > threshold


def compact_chat_message_text(
    text: str,
    *,
    threshold: int = LONG_CHAT_MESSAGE_THRESHOLD,
    shortcut_display: str | None = None,
) -> str:
    value = str(text or "")
    if not is_long_chat_message(value, threshold=threshold):
        return value
    shortcut = shortcut_display or display_for_action("open_long_chat_message", "Ctrl+Space")
    return (
        f"{value[:threshold]} ... "
        f"[wird in der mittleren Markdown/Mermaid-Ansicht fortgesetzt; {shortcut} erneut anzeigen]"
    )


def should_use_middle_view_for_message(message: dict[str, Any]) -> bool:
    sender_kind = str(message.get("sender_kind") or "user")
    return sender_kind in {"ai", "system"} and is_long_chat_message(str(message.get("text") or ""))


def latest_long_message_for_channel(channel: dict[str, Any]) -> dict[str, Any] | None:
    for msg in reversed([m for m in channel.get("messages") or [] if isinstance(m, dict)]):
        if should_use_middle_view_for_message(msg):
            return dict(msg)
    return None


def markdown_for_message(message: dict[str, Any], *, streaming: bool = False) -> str:
    sender = str(message.get("sender_id") or "unknown")
    sender_kind = str(message.get("sender_kind") or "message")
    text = str(message.get("text") or "")
    status = "> Antwortstream wird hier in der mittleren Ansicht fortgesetzt.\n\n" if streaming else ""
    return f"# Chat-Nachricht\n\n{status}**{sender_kind}** `{sender}`\n\n{text}"


def is_showing_chat_long_message(game: dict[str, Any]) -> bool:
    """True if the center view is currently showing a long chat message."""
    return bool(game.get("chat_long_message_id")) and bool(game.get("visual_viewport_enabled"))


def get_render_mode(game: dict[str, Any]) -> str:
    """Returns 'plain' or 'rendered'."""
    return "plain" if bool(game.get("markdown_stream_plain")) else "rendered"


def toggle_render_mode(game: dict[str, Any]) -> str:
    """Toggle between plain text and rendered Markdown/Mermaid. Returns new mode name."""
    new_plain = not bool(game.get("markdown_stream_plain"))
    game["markdown_stream_plain"] = new_plain
    game["markdown_auto_follow"] = False
    game["visual_viewport_force_render"] = True
    msg_id = str(game.get("chat_long_message_id") or "")
    suffix = "plain" if new_plain else "rendered"
    game["visual_state_version"] = f"chat-long-message:{msg_id}:{suffix}"
    return "plain" if new_plain else "rendered"


def configure_middle_view_for_message(
    game: dict[str, Any],
    message: dict[str, Any],
    *,
    channel_id: str,
    streaming: bool = False,
) -> bool:
    if not should_use_middle_view_for_message(message):
        return False
    text = str(message.get("text") or "")
    game["chat_long_message_markdown"] = markdown_for_message(message, streaming=streaming)
    game["chat_long_message_plain_text"] = text
    game["chat_long_message_id"] = str(message.get("id") or ("streaming" if streaming else ""))
    game["chat_long_message_channel"] = channel_id
    game["visual_viewport_enabled"] = True
    game["visual_viewport_active_view_request"] = "markdown_mermaid_document"
    game["visual_viewport_force_render"] = True
    game["markdown_auto_follow"] = True
    game["markdown_stream_plain"] = bool(streaming)
    game["markdown_mermaid_config"] = {
        "markdown_mode": "ansi",
        "mermaid_mode": "auto",
        "mermaid_renderers": ["mermaid_cli", "playwright", "fallback_codeblock"],
    }
    version_suffix = len(text)
    game["visual_state_version"] = f"chat-long-message:{game['chat_long_message_id']}:{version_suffix}"
    return True
