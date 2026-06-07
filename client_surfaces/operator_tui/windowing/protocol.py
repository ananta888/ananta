from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_ALLOWED_ACTIONS = frozenset(
    {
        "snake.pause",
        "snake.resume",
        "snake.follow.on",
        "snake.follow.off",
        "view.next",
        "view.previous",
        "view.simple",
        "view.doc",
        "view.snake",
        "focus.center",
        "focus.nav",
        "scroll.page_up",
        "scroll.page_down",
        "settings.reload",
    }
)


@dataclass(frozen=True)
class WindowActionEvent:
    action_id: str
    args: dict[str, Any]
    event_id: str
    source: str = "external_window"


def is_allowed_action(action_id: str) -> bool:
    return str(action_id or "").strip() in _ALLOWED_ACTIONS


def allowed_actions() -> tuple[str, ...]:
    return tuple(sorted(_ALLOWED_ACTIONS))
