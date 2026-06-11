"""Tutorial step definitions and validation — extracted from tutorial_ai_mixin.

Contains: _tutorial_target_label, _record_tutorial_propose_event,
          _tutorial_ai_target_cell, _step_toward_cell
"""
from __future__ import annotations

import json
from typing import Any


def _tutorial_target_label(
    *,
    board_w: int,
    board_h: int,
    target: tuple[int, int],
) -> str:
    tx, ty = int(target[0]), int(target[1])
    if ty <= max(2, board_h // 6):
        return "header"
    if tx <= max(2, board_w // 4):
        return "nav"
    if tx >= max(2, board_w - max(8, board_w // 4)) and ty >= max(2, board_h - max(6, board_h // 4)):
        return "detail"
    return "content"


def _record_tutorial_propose_event(
    game: dict[str, object],
    *,
    now: float,
    source: str,
    target: str,
    text: str,
) -> None:
    history_raw = game.get("tutorial_propose_history")
    history: list[dict[str, object]]
    if isinstance(history_raw, list):
        history = [dict(entry) for entry in history_raw if isinstance(entry, dict)]
    else:
        history = []
    entry: dict[str, Any] = {
        "at": float(now),
        "source": str(source or "unknown"),
        "target": str(target or "content"),
        "text": str(text or "").strip(),
    }
    if not entry["text"]:
        return
    last = history[-1] if history else None
    if isinstance(last, dict):
        if (
            str(last.get("source") or "") == str(entry["source"])
            and str(last.get("target") or "") == str(entry["target"])
            and str(last.get("text") or "") == str(entry["text"])
        ):
            return
    history.append(entry)
    game["tutorial_propose_history"] = history[-8:]


def _tutorial_ai_target_cell(
    *,
    board_w: int,
    board_h: int,
    context_tokens: list[str],
    local_head: tuple[int, int] | None,
) -> tuple[int, int]:
    text = " ".join(context_tokens).lower()
    if "target:header" in text:
        return (max(0, board_w - max(4, board_w // 6)), max(1, board_h // 6))
    if "target:nav" in text:
        return (max(1, board_w // 5), max(2, board_h // 2))
    if "target:content" in text:
        return (max(2, board_w // 2), max(2, board_h // 2))
    if "target:detail" in text:
        return (max(2, board_w - max(8, board_w // 4)), max(2, board_h - max(4, board_h // 4)))
    if "target:follow" in text and local_head is not None:
        return ((local_head[0] + 3) % max(1, board_w), local_head[1] % max(1, board_h))
    if any(token in text for token in ("endpoint", "auth", "header", "config", "oidc")):
        return (max(0, board_w - max(4, board_w // 6)), max(1, board_h // 6))
    if any(token in text for token in ("task", "goal", "section", "navigation", "queue")):
        return (max(1, board_w // 5), max(2, board_h // 2))
    if any(token in text for token in ("detail", "inspect", "artifact", "context", "result")):
        return (max(2, board_w - max(8, board_w // 4)), max(2, board_h - max(4, board_h // 4)))
    if local_head is not None:
        return ((local_head[0] + 3) % max(1, board_w), local_head[1] % max(1, board_h))
    return (max(2, board_w // 2), max(2, board_h // 2))


def _step_toward_cell(
    *,
    current: tuple[int, int],
    target: tuple[int, int],
    board_w: int,
    board_h: int,
) -> tuple[int, int]:
    cx, cy = int(current[0]), int(current[1])
    tx, ty = int(target[0]), int(target[1])
    bw = max(1, int(board_w))
    bh = max(1, int(board_h))
    raw_dx = (tx % bw) - (cx % bw)
    raw_dy = (ty % bh) - (cy % bh)
    dx = raw_dx
    dy = raw_dy
    if abs(raw_dx) > bw / 2:
        dx = raw_dx - bw if raw_dx > 0 else raw_dx + bw
    if abs(raw_dy) > bh / 2:
        dy = raw_dy - bh if raw_dy > 0 else raw_dy + bh
    if abs(dx) >= abs(dy) and dx != 0:
        step_x = 1 if dx > 0 else -1
        return ((cx + step_x) % bw, cy % bh)
    if dy != 0:
        step_y = 1 if dy > 0 else -1
        return (cx % bw, (cy + step_y) % bh)
    return (cx % bw, cy % bh)
