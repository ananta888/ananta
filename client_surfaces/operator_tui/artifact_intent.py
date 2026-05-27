"""Artifact intent detection and decision modeling for TUI snake.

ASH-042: intent, movement, target, and interaction are modeled as
separate, independent fields in SnakeArtifactDecision.

State transitions (from snake_state_catalog):
  MOVE_TO_ARTIFACT → EXPLAIN_ARTIFACT → CHAT_WITH_USER
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from client_surfaces.operator_tui.mouse import MouseState
from client_surfaces.operator_tui.region_index import RegionTarget


class IntentConfidence(str, Enum):
    NONE = "none"
    WEAK = "weak"
    LIKELY = "likely"
    CONFIRMED = "confirmed"


@dataclass(frozen=True)
class ArtifactIntent:
    confidence: IntentConfidence
    target: RegionTarget | None
    score: float
    reason: str


# ── ASH-042: Separated decision model ────────────────────────────────────────

class SnakeArtifactIntentKind(str, Enum):
    NONE            = "none"
    EXPLAIN         = "explain_artifact"
    INSPECT         = "inspect_artifact"
    MOVE_TO         = "move_to_artifact"
    CHAT            = "chat_with_user"


class SnakeArtifactMovement(str, Enum):
    NONE         = "none"
    FAST_TARGET  = "fast_target"
    FOLLOW_USER  = "follow_user"
    LURK         = "lurk"


class SnakeArtifactInteraction(str, Enum):
    NONE               = "none"
    OPEN_CHAT          = "open_chat_when_arrived"
    EXPLAIN_INLINE     = "explain_inline"
    SHOW_DETAIL        = "show_detail"


@dataclass
class SnakeArtifactDecision:
    """Fully separated intent/movement/target/interaction model (ASH-042).

    fast_target is only valid when target is not None.
    After arrival, intent transitions: MOVE_TO → EXPLAIN → CHAT.
    """
    intent: SnakeArtifactIntentKind = SnakeArtifactIntentKind.NONE
    movement: SnakeArtifactMovement = SnakeArtifactMovement.NONE
    target: RegionTarget | None = None
    interaction: SnakeArtifactInteraction = SnakeArtifactInteraction.NONE
    at_target: bool = False             # True once snake has reached target position

    def is_valid(self) -> bool:
        # fast_target requires a target
        if self.movement == SnakeArtifactMovement.FAST_TARGET and self.target is None:
            return False
        return True

    def next_intent_on_arrival(self) -> "SnakeArtifactDecision":
        """Advance intent after reaching target: MOVE_TO → EXPLAIN → CHAT."""
        import dataclasses
        if self.intent == SnakeArtifactIntentKind.MOVE_TO:
            return dataclasses.replace(
                self,
                intent=SnakeArtifactIntentKind.EXPLAIN,
                movement=SnakeArtifactMovement.NONE,
                at_target=True,
                interaction=self.interaction or SnakeArtifactInteraction.EXPLAIN_INLINE,
            )
        if self.intent == SnakeArtifactIntentKind.EXPLAIN:
            return dataclasses.replace(
                self,
                intent=SnakeArtifactIntentKind.CHAT,
                interaction=SnakeArtifactInteraction.OPEN_CHAT,
                at_target=True,
            )
        return self

    def to_game_dict(self) -> dict[str, Any]:
        return {
            "artifact_intent_kind": self.intent.value,
            "artifact_movement": self.movement.value,
            "artifact_intent_target": {
                "id": self.target.payload.get("id") or self.target.label,
                "section_id": self.target.section_id,
                "label": self.target.label,
            } if self.target else None,
            "artifact_interaction": self.interaction.value,
            "artifact_at_target": self.at_target,
        }


def build_snake_artifact_decision(
    intent: ArtifactIntent,
    *,
    at_target: bool = False,
) -> SnakeArtifactDecision:
    """Convert an ArtifactIntent (confidence-based) to a SnakeArtifactDecision."""
    if intent.confidence in (IntentConfidence.NONE, IntentConfidence.WEAK):
        return SnakeArtifactDecision()

    if at_target:
        return SnakeArtifactDecision(
            intent=SnakeArtifactIntentKind.EXPLAIN,
            movement=SnakeArtifactMovement.NONE,
            target=intent.target,
            interaction=SnakeArtifactInteraction.EXPLAIN_INLINE,
            at_target=True,
        )

    return SnakeArtifactDecision(
        intent=SnakeArtifactIntentKind.MOVE_TO,
        movement=SnakeArtifactMovement.FAST_TARGET,
        target=intent.target,
        interaction=SnakeArtifactInteraction.OPEN_CHAT,
        at_target=False,
    )


class ArtifactIntentDetector:
    def __init__(self, *, dwell_seconds: float = 0.35) -> None:
        self._dwell_seconds = max(0.1, min(2.0, float(dwell_seconds)))
        self._last_target_key: str = ""

    def evaluate(
        self,
        *,
        now: float,
        mouse: MouseState | None,
        target: RegionTarget | None,
        selected_index: int,
        current_section_id: str,
        user_feed: str,
    ) -> ArtifactIntent:
        if target is None or mouse is None or not mouse.active:
            self._last_target_key = ""
            return ArtifactIntent(confidence=IntentConfidence.NONE, target=None, score=0.0, reason="no-target")

        score = 0.0
        reason = ["hit"]
        target_key = f"{target.section_id}:{target.kind}:{target.payload.get('id') or target.payload.get('path') or target.label}"
        if target_key != self._last_target_key:
            self._last_target_key = target_key

        if target.kind in {"artifact", "item"}:
            score += 0.6
            reason.append("content-item")
        elif target.kind in {"section", "pane"}:
            score += 0.2
            reason.append(target.kind)

        hover_age = max(0.0, now - float(mouse.hover_started_at or now))
        if hover_age >= self._dwell_seconds:
            score += 0.55
            reason.append("hover-dwell")
        elif hover_age >= (self._dwell_seconds * 0.5):
            score += 0.25
            reason.append("hover-warm")

        if mouse.last_event_type == "down":
            score += 1.0
            reason.append("click")
        elif mouse.last_event_type in {"scroll_up", "scroll_down"}:
            score += 0.15
            reason.append("scroll")

        if target.section_id == current_section_id:
            score += 0.2
            reason.append("same-section")
        if int(target.payload.get("selected_index", -1)) == int(selected_index):
            score += 0.2
            reason.append("selection-agree")
        if target.label and target.label.lower() in str(user_feed or "").lower():
            score += 0.25
            reason.append("feed-match")

        if score >= 1.55:
            confidence = IntentConfidence.CONFIRMED
        elif score >= 1.0:
            confidence = IntentConfidence.LIKELY
        elif score >= 0.4:
            confidence = IntentConfidence.WEAK
        else:
            confidence = IntentConfidence.NONE
        return ArtifactIntent(confidence=confidence, target=target, score=score, reason=",".join(reason))
