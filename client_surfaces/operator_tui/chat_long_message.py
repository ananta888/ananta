from __future__ import annotations

import hashlib
import time
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


def _markdown_config_for_mode(*, rendered: bool) -> dict[str, Any]:
    return {
        "markdown_mode": "ansi",
        "mermaid_mode": "auto" if rendered else "disabled",
        "mermaid_renderers": ["mermaid_cli", "playwright", "fallback_codeblock"],
    }


def _stable_message_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def toggle_render_mode(game: dict[str, Any]) -> str:
    """Toggle between plain text and rendered Markdown/Mermaid. Returns new mode name."""
    new_plain = not bool(game.get("markdown_stream_plain"))
    game["markdown_stream_plain"] = new_plain
    game["markdown_auto_follow"] = False
    game["visual_viewport_force_render"] = True
    game["markdown_mermaid_render_requested"] = not new_plain
    game["markdown_mermaid_config"] = _markdown_config_for_mode(rendered=not new_plain)
    msg_id = str(game.get("chat_long_message_id") or "")
    suffix = "plain" if new_plain else "rendered"
    game["visual_state_version"] = f"chat-long-message:{msg_id}:{suffix}"
    return "plain" if new_plain else "rendered"


def refresh_rendered_view(game: dict[str, Any]) -> None:
    """Force refresh for the rendered long-message view without losing the original text."""
    game["markdown_stream_plain"] = False
    game["markdown_mermaid_render_requested"] = True
    game["markdown_mermaid_config"] = _markdown_config_for_mode(rendered=True)
    game["markdown_auto_follow"] = False
    game["visual_viewport_force_render"] = True
    game["visual_viewport_frame_lines"] = []
    msg_id = str(game.get("chat_long_message_id") or "")
    game["visual_state_version"] = f"chat-long-message:{msg_id}:rendered-refresh:{time.time():.6f}"


def remember_long_message(game: dict[str, Any], message: dict[str, Any], *, channel_id: str) -> None:
    """Cache original long-message output for the left-menu history tree."""
    text = str(message.get("text") or "")
    if not text:
        return
    msg_id = str(message.get("id") or _stable_message_id("msg", channel_id, text))
    history_raw = game.get("chat_long_message_history")
    history = [dict(item) for item in history_raw if isinstance(item, dict)] if isinstance(history_raw, list) else []
    entry = {
        "id": msg_id,
        "channel_id": str(channel_id or "room:main"),
        "sender_id": str(message.get("sender_id") or "unknown"),
        "sender_kind": str(message.get("sender_kind") or "message"),
        "text": text,
        "markdown": markdown_for_message(message, streaming=False),
        "created_at": float(message.get("created_at") or time.time()),
        "preview": " ".join(text.split())[:80],
    }
    history = [item for item in history if str(item.get("id") or "") != msg_id]
    history.append(entry)
    game["chat_long_message_history"] = history[-50:]


def long_message_history_rows(game: dict[str, Any]) -> list[dict[str, Any]]:
    raw = game.get("chat_long_message_history")
    rows = [dict(item) for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
    rows.sort(key=lambda item: float(item.get("created_at") or 0), reverse=True)
    return rows


def configure_middle_view_for_history_entry(game: dict[str, Any], entry: dict[str, Any]) -> bool:
    text = str(entry.get("text") or "")
    if not text:
        return False
    msg_id = str(entry.get("id") or _stable_message_id("history", text))
    game["chat_long_message_markdown"] = str(entry.get("markdown") or markdown_for_message(entry, streaming=False))
    game["chat_long_message_plain_text"] = text
    game["chat_long_message_id"] = msg_id
    game["chat_long_message_channel"] = str(entry.get("channel_id") or "room:main")
    game["visual_viewport_enabled"] = True
    game["visual_viewport_active_view_request"] = "markdown_mermaid_document"
    game["visual_viewport_force_render"] = True
    game["markdown_auto_follow"] = False
    game["markdown_stream_plain"] = True
    game["markdown_mermaid_render_requested"] = False
    game["markdown_mermaid_config"] = _markdown_config_for_mode(rendered=False)
    game["visual_state_version"] = f"chat-long-message:{msg_id}:plain-history"
    return True


def configure_middle_view_for_message(
    game: dict[str, Any],
    message: dict[str, Any],
    *,
    channel_id: str,
    streaming: bool = False,
    activate_view: bool = True,
) -> bool:
    if not should_use_middle_view_for_message(message):
        return False
    text = str(message.get("text") or "")
    remember_long_message(game, message, channel_id=channel_id)
    if not activate_view:
        return True
    game["chat_long_message_markdown"] = markdown_for_message(message, streaming=streaming)
    game["chat_long_message_plain_text"] = text
    game["chat_long_message_id"] = str(message.get("id") or ("streaming" if streaming else ""))
    game["chat_long_message_channel"] = channel_id
    game["visual_viewport_enabled"] = True
    game["visual_viewport_active_view_request"] = "markdown_mermaid_document"
    game["visual_viewport_force_render"] = True
    game["markdown_auto_follow"] = True
    game["markdown_stream_plain"] = True
    game["markdown_mermaid_render_requested"] = False
    game["markdown_mermaid_config"] = _markdown_config_for_mode(rendered=False)
    version_suffix = len(text)
    game["visual_state_version"] = f"chat-long-message:{game['chat_long_message_id']}:plain:{version_suffix}"
    return True
