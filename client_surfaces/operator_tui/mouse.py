from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Literal

MouseEventType = Literal["move", "down", "up", "scroll_up", "scroll_down", "unknown"]


@dataclass(frozen=True)
class MouseState:
    x: int = 0
    y: int = 0
    last_event_type: MouseEventType = "unknown"
    buttons: int = 0
    scroll_delta: int = 0
    last_seen_at: float = 0.0
    active: bool = False
    hover_started_at: float = 0.0


def detect_mouse_support(env: dict[str, str] | None = None) -> dict[str, object]:
    values = dict(env or os.environ)
    term = str(values.get("TERM") or "").strip().lower()
    term_program = str(values.get("TERM_PROGRAM") or "").strip()
    force = str(values.get("ANANTA_TUI_MOUSE", "")).strip().lower()
    if force in {"0", "false", "no", "off"}:
        enabled = False
        reason = "disabled-by-env"
    elif force in {"1", "true", "yes", "on"}:
        enabled = True
        reason = "enabled-by-env"
    else:
        enabled = bool(term) and (
            "xterm" in term
            or "screen" in term
            or "tmux" in term
            or "vt100" in term
            or term_program in {"vscode", "iTerm.app", "Windows_Terminal"}
            or bool(values.get("WT_SESSION"))
        )
        reason = "terminal-detected" if enabled else "terminal-unknown"
    return {
        "enabled": enabled,
        "reason": reason,
        "term": term or "(unset)",
        "term_program": term_program or "(unset)",
    }


def clamp_mouse_coords(x: int, y: int, *, width: int, height: int) -> tuple[int, int]:
    w = max(1, int(width))
    h = max(1, int(height))
    return max(0, min(w - 1, int(x))), max(0, min(h - 1, int(y)))


def normalize_mouse_state(
    previous: MouseState | None,
    *,
    x: int,
    y: int,
    width: int,
    height: int,
    event_type: MouseEventType,
    buttons: int = 0,
    scroll_delta: int = 0,
    now: float | None = None,
) -> MouseState:
    px, py = clamp_mouse_coords(x, y, width=width, height=height)
    ts = float(now if now is not None else time.monotonic())
    prev = previous or MouseState()
    hover_started = prev.hover_started_at if (prev.active and prev.x == px and prev.y == py) else ts
    return MouseState(
        x=px,
        y=py,
        last_event_type=event_type,
        buttons=max(0, int(buttons)),
        scroll_delta=int(scroll_delta),
        last_seen_at=ts,
        active=True,
        hover_started_at=hover_started,
    )
