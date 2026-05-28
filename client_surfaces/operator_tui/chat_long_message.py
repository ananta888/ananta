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
    return f"{value[:threshold]} ... [{shortcut}: Rest im mittleren Markdown/Mermaid-Bereich]"


def latest_long_message_for_channel(channel: dict[str, Any]) -> dict[str, Any] | None:
    for msg in reversed([m for m in channel.get("messages") or [] if isinstance(m, dict)]):
        if is_long_chat_message(str(msg.get("text") or "")):
            return dict(msg)
    return None


def markdown_for_message(message: dict[str, Any]) -> str:
    sender = str(message.get("sender_id") or "unknown")
    sender_kind = str(message.get("sender_kind") or "message")
    text = str(message.get("text") or "")
    return f"# Chat-Nachricht\n\n**{sender_kind}** `{sender}`\n\n{text}"
