from __future__ import annotations

import time
from typing import Any


def make_follow_state(
    *,
    ai_position: tuple[int, int] = (0, 0),
    mode: str = "lurking_follow",
    follow_distance: int = 4,
    linger_distance: int = 6,
    speed_factor: float = 0.45,
) -> dict[str, Any]:
    return {
        "mode": mode,
        "ai_position": ai_position,
        "target_position": ai_position,
        "follow_distance": max(1, int(follow_distance)),
        "linger_distance": max(1, int(linger_distance)),
        "speed_factor": max(0.1, min(1.0, float(speed_factor))),
        "last_user_positions": [],
        "prediction_target": None,
        "updated_at": time.time(),
    }


def step_follow_state(
    state: dict[str, Any],
    *,
    user_position: tuple[int, int],
    board_w: int,
    board_h: int,
) -> dict[str, Any]:
    follow_distance = int(state.get("follow_distance") or 4)
    linger_distance = int(state.get("linger_distance") or 6)
    ai_x, ai_y = _xy(state.get("ai_position"))
    user_x, user_y = int(user_position[0]), int(user_position[1])
    bw = max(1, int(board_w))
    bh = max(1, int(board_h))
    raw_dx = (user_x % bw) - (ai_x % bw)
    raw_dy = (user_y % bh) - (ai_y % bh)
    dx = raw_dx
    dy = raw_dy
    if abs(raw_dx) > bw / 2:
        dx = raw_dx - bw if raw_dx > 0 else raw_dx + bw
    if abs(raw_dy) > bh / 2:
        dy = raw_dy - bh if raw_dy > 0 else raw_dy + bh
    manhattan = abs(dx) + abs(dy)

    mode = str(state.get("mode") or "lurking_follow")
    if mode == "off":
        return {**state, "updated_at": time.time()}
    if manhattan <= follow_distance:
        mode = "lurking"
    elif manhattan >= linger_distance:
        mode = "follow"

    next_pos = (ai_x, ai_y)
    if mode == "follow":
        if abs(dx) >= abs(dy) and dx != 0:
            next_pos = ((ai_x + (1 if dx > 0 else -1)) % bw, ai_y % bh)
        elif dy != 0:
            next_pos = (ai_x % bw, (ai_y + (1 if dy > 0 else -1)) % bh)

    history = list(state.get("last_user_positions") or [])
    history.append((user_x % bw, user_y % bh))
    history = history[-20:]
    return {
        **state,
        "mode": mode,
        "ai_position": next_pos,
        "target_position": (user_x, user_y),
        "last_user_positions": history,
        "updated_at": time.time(),
    }


def apply_worker_follow_update(
    state: dict[str, Any],
    *,
    follow_mode_update: str,
    prediction_target: str | None,
    confidence: float,
) -> dict[str, Any]:
    mode = str(follow_mode_update or "").strip().lower()
    allowed = {"follow", "lurking", "point_to_target", "lurking_follow"}
    if mode not in allowed:
        mode = str(state.get("mode") or "lurking_follow")
    out = dict(state)
    out["mode"] = mode
    out["updated_at"] = time.time()
    if prediction_target and float(confidence) >= 0.65:
        out["prediction_target"] = str(prediction_target)
    return out


def _xy(raw: Any) -> tuple[int, int]:
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        return int(raw[0]), int(raw[1])
    return (0, 0)
