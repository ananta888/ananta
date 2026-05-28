"""DecisionResult — gemeinsames Ergebnis-Modell für Snake und Chat Heuristiken.

Vereinheitlicht die drei vorhandenen PolicyDecision-Varianten:
- ai_snake_policy.py PolicyDecision
- chat_policy.py Entscheidungen
- diverse agent/services Variants

Bestehende PolicyDecision-Instanzen können via to_decision_result() konvertiert
werden — kein Breaking Change.

Schema: schemas/heuristic/decision_result.v1.json
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SuggestedMotion:
    dx: int = 0
    dy: int = 0

    def to_dict(self) -> dict[str, int]:
        return {"dx": self.dx, "dy": self.dy}


@dataclass
class DecisionResult:
    action_kind: str
    confidence: float
    source: str  # ai | heuristic | hybrid
    answer_kind: str | None = None
    selected_context_refs: list[str] = field(default_factory=list)
    suggested_motion: SuggestedMotion | None = None
    answer_blocks: list[dict[str, Any]] = field(default_factory=list)
    reason_codes: list[str] = field(default_factory=list)
    strategy_id: str | None = None
    rule_id: str | None = None
    fallback_reason: str | None = None

    def is_heuristic(self) -> bool:
        return self.source == "heuristic"

    def is_no_good_match(self) -> bool:
        return self.answer_kind == "no_good_match"

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_kind": self.action_kind,
            "answer_kind": self.answer_kind,
            "confidence": self.confidence,
            "selected_context_refs": list(self.selected_context_refs),
            "suggested_motion": self.suggested_motion.to_dict() if self.suggested_motion else None,
            "answer_blocks": list(self.answer_blocks),
            "reason_codes": list(self.reason_codes),
            "source": self.source,
            "strategy_id": self.strategy_id,
            "rule_id": self.rule_id,
            "fallback_reason": self.fallback_reason,
        }

    @staticmethod
    def heuristic_follow(*, dx: int = 0, dy: int = 0, strategy_id: str | None = None) -> "DecisionResult":
        return DecisionResult(
            action_kind="follow",
            confidence=1.0,
            source="heuristic",
            suggested_motion=SuggestedMotion(dx=dx, dy=dy),
            strategy_id=strategy_id,
        )

    @staticmethod
    def heuristic_lurk(*, strategy_id: str | None = None) -> "DecisionResult":
        return DecisionResult(
            action_kind="lurk",
            confidence=1.0,
            source="heuristic",
            strategy_id=strategy_id,
        )

    @staticmethod
    def no_good_match() -> "DecisionResult":
        return DecisionResult(
            action_kind="no_action",
            answer_kind="no_good_match",
            confidence=0.0,
            source="heuristic",
            reason_codes=["no_context_found"],
        )

    @staticmethod
    def policy_denied(reason: str) -> "DecisionResult":
        return DecisionResult(
            action_kind="policy_denied",
            confidence=1.0,
            source="heuristic",
            reason_codes=[reason],
            fallback_reason="policy_denied",
        )

    @staticmethod
    def fallback(*, reason: str, strategy_id: str | None = None) -> "DecisionResult":
        return DecisionResult(
            action_kind="follow",
            confidence=0.5,
            source="heuristic",
            fallback_reason=reason,
            strategy_id=strategy_id,
            reason_codes=[f"fallback:{reason}"],
        )


    @staticmethod
    def from_dsl_action(action: dict, *, strategy_id: str | None = None) -> "DecisionResult":
        """Erzeugt DecisionResult aus DSL v2 action dict."""
        kind = action.get("kind", "no_action")
        confidence = float(action.get("confidence", 0.8))
        reason_codes = list(action.get("reason_codes") or [])

        if kind in ("suggest_target", "fast_target", "smooth_follow", "follow_artifact"):
            motion = None
            target_cell = action.get("target_cell")
            if target_cell:
                motion = SuggestedMotion(dx=int(target_cell.get("x", 0)), dy=int(target_cell.get("y", 0)))
            else:
                target_bbox = action.get("target_bbox")
                if target_bbox:
                    cx = int(target_bbox.get("x", 0)) + int(target_bbox.get("w", 0)) // 2
                    cy = int(target_bbox.get("y", 0)) + int(target_bbox.get("h", 0)) // 2
                    motion = SuggestedMotion(dx=cx, dy=cy)
            return DecisionResult(
                action_kind="follow", confidence=confidence, source="heuristic",
                suggested_motion=motion, reason_codes=reason_codes, strategy_id=strategy_id,
            )
        if kind == "lurk_near":
            return DecisionResult.heuristic_lurk(strategy_id=strategy_id)
        if kind == "explain_target":
            return DecisionResult(
                action_kind="follow", confidence=confidence, source="heuristic",
                reason_codes=["explain_target"] + reason_codes, strategy_id=strategy_id,
            )
        # no_action
        return DecisionResult.no_good_match()


def from_ai_snake_policy_decision(pd: Any) -> DecisionResult:
    """Adapter: konvertiert ai_snake_policy.PolicyDecision zu DecisionResult."""
    allowed = bool(getattr(pd, "allowed", True))
    reason_code = str(getattr(pd, "reason_code", "") or "")
    if not allowed:
        return DecisionResult.policy_denied(reason_code or "policy_blocked")
    return DecisionResult(
        action_kind="follow",
        confidence=1.0,
        source="heuristic",
        reason_codes=[reason_code] if reason_code else [],
    )
