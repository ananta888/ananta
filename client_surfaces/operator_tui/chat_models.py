"""chat_models.py — Core value types for the chat pipeline.

Contains enums, dataclasses, and message/answer-block factories that have
no dependencies on other chat_* modules in this package.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── ChatState type alias ──────────────────────────────────────────────────────
#: Structural type alias for the top-level chat state dict.  Used in
#: TYPE_CHECKING imports across operator_tui.
ChatState = dict[str, Any]


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
