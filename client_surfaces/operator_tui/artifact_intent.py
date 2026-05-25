from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

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
