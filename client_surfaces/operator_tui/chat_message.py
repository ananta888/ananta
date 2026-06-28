"""chat_message.py — Message append, sanitize, and unread helpers.

Contains: append_message, sanitize_text, unread_total,
          maybe_add_prediction_comment, append_artifact_graph_explanation.

make_message is defined in chat_models and re-exported here for
convenience so callers can import from one place.
"""
from __future__ import annotations

from typing import Any

from client_surfaces.operator_tui.chat_models import (
    ChannelType,
    DeliveryState,
    SenderKind,
    Visibility,
    make_message,
)

# Re-export make_message so callers that import from chat_message keep working.
__all__ = [
    "make_message",
    "append_message",
    "sanitize_text",
    "unread_total",
    "maybe_add_prediction_comment",
    "append_artifact_graph_explanation",
]

# ── Message helpers ───────────────────────────────────────────────────────────

_MAX_MESSAGES = 200


def append_message(chat: dict[str, Any], msg: dict[str, Any]) -> None:
    ch_id = str(msg.get("channel_id") or "")
    channels = chat.setdefault("channels", {})
    if ch_id not in channels:
        return
    ch = channels[ch_id]
    msgs: list[dict[str, Any]] = list(ch.get("messages") or [])
    # deduplicate by id
    existing_ids = {m.get("id") for m in msgs}
    if msg.get("id") in existing_ids:
        return
    msgs.append(msg)
    if len(msgs) > _MAX_MESSAGES:
        msgs = msgs[-_MAX_MESSAGES:]
    ch["messages"] = msgs
    # bump unread if channel not active
    if ch_id != chat.get("active_channel"):
        ch["unread"] = int(ch.get("unread") or 0) + 1


def sanitize_text(text: str, *, max_len: int | None = 500) -> str:
    """Strip ANSI and control chars from user-supplied message text."""
    import re
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    cleaned = text.strip()
    if isinstance(max_len, int) and max_len >= 0:
        return cleaned[:max_len]
    return cleaned


def unread_total(chat: dict[str, Any]) -> int:
    return sum(int(ch.get("unread") or 0) for ch in (chat.get("channels") or {}).values())


def maybe_add_prediction_comment(
    chat: dict[str, Any],
    *,
    prediction: dict[str, Any],
    now: float,
    quiet: bool,
    forced: bool = False,
    cooldown_seconds: int = 20,
) -> bool:
    """Add a compact proactive AI comment only when policy gates allow it."""
    if quiet and not forced:
        return False
    confidence = float(prediction.get("confidence") or 0.0)
    if confidence < 0.65 and not forced:
        return False
    last_comment_at = float(chat.get("ai_last_proactive_comment_at") or 0.0)
    if (now - last_comment_at) < max(5, int(cooldown_seconds)) and not forced:
        return False
    intent = str(prediction.get("predicted_intent") or "unknown")
    target_ref = str(prediction.get("target_ref") or "").strip()
    if not target_ref:
        target_ref = "aktuellen Bereich"
    text = f"Ich glaube, du willst zu {target_ref} ({intent}, conf={confidence:.2f})."
    msg = make_message(
        channel_id="ai:tutor",
        channel_type=ChannelType.AI,
        sender_id="s-ai",
        sender_kind=SenderKind.AI,
        text=text,
        delivery_state=DeliveryState.RECEIVED,
    )
    append_message(chat, msg)
    chat["ai_last_proactive_comment_at"] = float(now)
    return True


def append_artifact_graph_explanation(chat: dict[str, Any], *, text: str, goal_id: str) -> None:
    """Persist AI artifact-graph explanation in ai channel and keep it metadata-only."""
    sanitized = sanitize_text(text)
    if not sanitized:
        return
    msg = make_message(
        channel_id="ai:tutor",
        channel_type=ChannelType.AI,
        sender_id="s-ai",
        sender_kind=SenderKind.AI,
        text=f"[artifact-graph:{goal_id}] {sanitized}",
        delivery_state=DeliveryState.RECEIVED,
        visibility=Visibility.AI_CONTEXT,
    )
    append_message(chat, msg)
