"""T02.01 + T02.03: Chat-State – Channel-Modell und ChatMessage-v1.

Channel types: room, direct, ai, notes, system
Message fields: id, created_at, channel_id, channel_type, sender_id, sender_kind,
                target_ids, text, visibility, delivery_state, policy_decision_ref
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── Enums ─────────────────────────────────────────────────────────────────────


class ChannelType(str, Enum):
    ROOM = "room"
    DIRECT = "direct"
    AI = "ai"
    NOTES = "notes"
    SYSTEM = "system"


class Visibility(str, Enum):
    LOCAL_ONLY = "local_only"
    ROOM = "room"
    DIRECT = "direct"
    AI_CONTEXT = "ai_context"
    SYSTEM = "system"


class DeliveryState(str, Enum):
    DRAFT = "draft"
    QUEUED = "queued"
    SENT = "sent"
    RECEIVED = "received"
    FAILED = "failed"
    BLOCKED = "blocked"


class SenderKind(str, Enum):
    USER = "user"
    AI = "ai"
    SYSTEM = "system"


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


# ── ChatMessage factory ───────────────────────────────────────────────────────


def make_message(
    *,
    channel_id: str,
    channel_type: str,
    sender_id: str,
    text: str,
    sender_kind: str = SenderKind.USER,
    target_ids: list[str] | None = None,
    visibility: str | None = None,
    delivery_state: str = DeliveryState.DRAFT,
    policy_decision_ref: str | None = None,
) -> dict[str, Any]:
    if visibility is None:
        if channel_type == ChannelType.NOTES:
            visibility = Visibility.LOCAL_ONLY
        elif channel_type == ChannelType.AI:
            visibility = Visibility.AI_CONTEXT
        elif channel_type == ChannelType.DIRECT:
            visibility = Visibility.DIRECT
        else:
            visibility = Visibility.ROOM
    return {
        "id": str(uuid.uuid4()),
        "created_at": time.time(),
        "channel_id": channel_id,
        "channel_type": channel_type,
        "sender_id": sender_id,
        "sender_kind": sender_kind,
        "target_ids": target_ids or [],
        "text": str(text)[:500],
        "visibility": visibility,
        "delivery_state": delivery_state,
        "policy_decision_ref": policy_decision_ref,
    }


# ── Chat state (top-level game dict key: "chat_state") ────────────────────────


def default_chat_state(local_snake_id: str = "s1") -> dict[str, Any]:
    return {
        "local_snake_id": local_snake_id,
        "active_channel": "room:main",
        "channels": default_channels(),
        "chat_focus": False,
        "chat_input_buffer": "",
        "notes_context_released": False,
        "ai_typing": False,
    }


def get_chat_state(game: dict[str, Any]) -> dict[str, Any]:
    raw = game.get("chat_state")
    if isinstance(raw, dict):
        return raw
    local_id = str(game.get("local_snake_id") or "s1")
    return default_chat_state(local_id)


# ── ChatAnswerBlock ───────────────────────────────────────────────────────────

@dataclass
class SourceRef:
    """A context reference that can be opened in the TUI."""
    ref: str
    display_label: str = ""
    openable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"ref": self.ref, "display_label": self.display_label or self.ref, "openable": self.openable}


@dataclass
class ChatAnswerBlock:
    """Structured heuristic answer, always marked as is_heuristic=True.

    Displayed with '[Heuristik]' / '~' prefix in TUI.
    source_refs are openable (Enter/Click) in TUI.
    """
    result_text: str
    source_refs: list[SourceRef] = field(default_factory=list)
    why_these_sources: str = ""       # max 200 chars
    next_steps: list[str] = field(default_factory=list)  # max 3
    uncertainty_note: str = ""        # set when confidence < 0.7
    is_heuristic: bool = True
    confidence: float = 1.0

    @property
    def tui_prefix(self) -> str:
        return "[Heuristik] " if self.is_heuristic else ""

    @property
    def tui_text(self) -> str:
        return f"{self.tui_prefix}{self.result_text}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_text": self.result_text,
            "tui_text": self.tui_text,
            "source_refs": [r.to_dict() for r in self.source_refs],
            "why_these_sources": self.why_these_sources[:200],
            "next_steps": self.next_steps[:3],
            "uncertainty_note": self.uncertainty_note,
            "is_heuristic": self.is_heuristic,
            "confidence": self.confidence,
        }

    @staticmethod
    def no_good_match() -> "ChatAnswerBlock":
        return ChatAnswerBlock(
            result_text="Kein passender Kontext gefunden.",
            source_refs=[],
            why_these_sources="",
            next_steps=["Frag den AI-Assistenten", "Lade passendes Artefakt"],
            is_heuristic=True,
            confidence=0.0,
        )

    @staticmethod
    def from_decision_result(result: Any, *, confidence: float = 1.0) -> "ChatAnswerBlock":
        """Build from a DecisionResult with answer_kind and selected_context_refs."""
        refs = [SourceRef(ref=r) for r in getattr(result, "selected_context_refs", [])]
        text = "Kontext ausgewählt." if refs else "Kein passender Kontext gefunden."
        note = "" if confidence >= 0.7 else f"Unsicher (confidence={confidence:.2f})"
        return ChatAnswerBlock(
            result_text=text,
            source_refs=refs,
            why_these_sources=f"intent-basierte Selektion, {len(refs)} Refs",
            next_steps=[],
            uncertainty_note=note,
            is_heuristic=True,
            confidence=confidence,
        )


def make_heuristic_message(
    *,
    channel_id: str,
    sender_id: str,
    answer_block: ChatAnswerBlock,
    policy_decision_ref: str | None = None,
) -> dict[str, Any]:
    """Create a chat message carrying a heuristic answer block."""
    msg = make_message(
        channel_id=channel_id,
        channel_type=ChannelType.AI,
        sender_id=sender_id,
        sender_kind=SenderKind.AI,
        text=answer_block.tui_text[:500],
        policy_decision_ref=policy_decision_ref,
    )
    msg["answer_block"] = answer_block.to_dict()
    msg["is_heuristic"] = True
    return msg


def set_chat_state(game: dict[str, Any], chat: dict[str, Any]) -> None:
    game["chat_state"] = chat


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


def sanitize_text(text: str) -> str:
    """Strip ANSI and control chars from user-supplied message text."""
    import re
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return text.strip()[:500]


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
