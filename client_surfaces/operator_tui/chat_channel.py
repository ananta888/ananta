"""chat_channel.py — Channel factory and channel helpers.

Contains: make_channel, default_channels, get_channel, get_active_channel,
          switch_channel, add_direct_channel.
"""
from __future__ import annotations

from typing import Any

from client_surfaces.operator_tui.chat_models import ChannelType, Visibility


# ── Persistence policy ────────────────────────────────────────────────────────


_PERSISTENCE_POLICY: dict[str, str] = {
    ChannelType.ROOM: "hub",
    ChannelType.DIRECT: "hub",
    ChannelType.AI: "local",
    ChannelType.NOTES: "local_only",
    ChannelType.SYSTEM: "ephemeral",
}


# ── Channel factory ───────────────────────────────────────────────────────────


def make_channel(
    channel_id: str,
    channel_type: str,
    display_name: str,
    participants: list[str] | None = None,
    visibility: str | None = None,
) -> dict[str, Any]:
    if visibility is None:
        if channel_type == ChannelType.NOTES:
            visibility = Visibility.LOCAL_ONLY
        elif channel_type == ChannelType.AI:
            visibility = Visibility.AI_CONTEXT
        else:
            visibility = Visibility.ROOM
    return {
        "id": channel_id,
        "channel_type": channel_type,
        "display_name": display_name,
        "visibility": visibility,
        "participants": participants or [],
        "persistence_policy": _PERSISTENCE_POLICY.get(channel_type, "local"),
        "unread": 0,
        "messages": [],
        "scroll_offset": 0,
    }


# ── Default channels ──────────────────────────────────────────────────────────


def default_channels() -> dict[str, dict[str, Any]]:
    return {
        "room:main": make_channel("room:main", ChannelType.ROOM, "#room"),
        "ai:tutor": make_channel("ai:tutor", ChannelType.AI, "AI tutor-ai", participants=["s-ai"]),
        "notes:self": make_channel("notes:self", ChannelType.NOTES, "notes local-only", participants=["self"]),
        "system": make_channel("system", ChannelType.SYSTEM, "system"),
    }


# ── Channel helpers ───────────────────────────────────────────────────────────


def get_channel(chat: dict[str, Any], channel_id: str) -> dict[str, Any] | None:
    channels = chat.get("channels") or {}
    return channels.get(channel_id)


def get_active_channel(chat: dict[str, Any]) -> dict[str, Any] | None:
    return get_channel(chat, str(chat.get("active_channel") or "room:main"))


def switch_channel(chat: dict[str, Any], channel_id: str, *, preserve_input: bool = False) -> bool:
    if channel_id not in (chat.get("channels") or {}):
        return False
    ch = chat["channels"][channel_id]
    ch["unread"] = 0
    chat["active_channel"] = channel_id
    chat["scroll_offset"] = 0
    if not preserve_input:
        chat["chat_input_buffer"] = ""
    return True


def add_direct_channel(chat: dict[str, Any], snake_id: str, display_name: str) -> str:
    ch_id = f"direct:{snake_id}"
    if ch_id not in (chat.get("channels") or {}):
        chat.setdefault("channels", {})[ch_id] = make_channel(
            ch_id, ChannelType.DIRECT, f"@{display_name}", participants=[snake_id]
        )
    return ch_id
