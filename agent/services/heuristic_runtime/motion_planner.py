"""Motion Planner — berechnet dx/dy aus DSL-Action und Snake-Kopfposition."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.services.heuristic_runtime.decision_result import SuggestedMotion


@dataclass
class MotionPlan:
    dx: int
    dy: int
    strategy: str = "unknown"
    confidence: float = 1.0
    clamped: bool = False


class MotionPlanner:
    """Deterministischer Motion-Planer: DSL-Action + Snake-Kopf → dx/dy."""

    def plan(
        self,
        action: dict[str, Any],
        snake_head: tuple[int, int],
        *,
        board_w: int = 120,
        board_h: int = 32,
    ) -> MotionPlan:
        kind = action.get("kind", "no_action")
        max_step = int(action.get("max_step", 2))
        min_distance = int(action.get("min_distance", 0))

        if kind == "no_action":
            return MotionPlan(dx=0, dy=0, strategy="no_action", confidence=0.0)

        target_cell = action.get("target_cell")
        target_bbox = action.get("target_bbox")

        # Zielkoordinaten ermitteln
        target_x: int | None = None
        target_y: int | None = None

        if target_cell:
            target_x = int(target_cell.get("x", snake_head[0]))
            target_y = int(target_cell.get("y", snake_head[1]))
        elif target_bbox:
            target_x = int(target_bbox.get("x", snake_head[0])) + int(target_bbox.get("w", 0)) // 2
            target_y = int(target_bbox.get("y", snake_head[1])) + int(target_bbox.get("h", 0)) // 2

        if target_x is None or target_y is None:
            # follow/lurk ohne Ziel: geradeaus halten
            return MotionPlan(dx=1, dy=0, strategy=kind, confidence=0.5)

        hx, hy = snake_head
        raw_dx = target_x - hx
        raw_dy = target_y - hy
        dist = abs(raw_dx) + abs(raw_dy)

        if dist <= min_distance:
            return MotionPlan(dx=0, dy=0, strategy="already_near", confidence=1.0)

        # Normieren
        dx = _sign(raw_dx) if raw_dx != 0 else 0
        dy = _sign(raw_dy) if raw_dy != 0 else 0

        # Bevorzuge eine Achse (Manhattan-Schritt)
        if abs(raw_dx) >= abs(raw_dy):
            dy = 0
        else:
            dx = 0

        # max_step begrenzen
        dx = max(-max_step, min(max_step, dx))
        dy = max(-max_step, min(max_step, dy))

        # Board-Grenzen: kein Sprung außerhalb
        new_x = hx + dx
        new_y = hy + dy
        clamped = False
        if not (0 <= new_x < board_w):
            dx = 0
            clamped = True
        if not (0 <= new_y < board_h):
            dy = 0
            clamped = True

        strategy = "fast_target" if kind == "fast_target" else "smooth_follow"
        return MotionPlan(dx=dx, dy=dy, strategy=strategy, confidence=action.get("confidence", 0.8), clamped=clamped)


def _sign(x: int) -> int:
    return 1 if x > 0 else -1 if x < 0 else 0
