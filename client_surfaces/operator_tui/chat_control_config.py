from __future__ import annotations

import os
from dataclasses import dataclass

_VALID_MODES = frozenset({"interactive_safe", "autonomous_e2e"})

_DEFAULT_E2E_ALLOWLIST: tuple[str, ...] = (
    "help.tui",
    "view.list",
    "view.next",
    "view.previous",
    "view.select",
    "overlay.views.on",
    "overlay.views.off",
    "overlay.views.toggle",
    "focus.chat",
    "focus.artifacts",
    "focus.main",
    "focus.diagnostics",
    "focus.center",
    "focus.logs",
    "focus.nav",
    "snake.pause",
    "snake.resume",
    "snake.follow.on",
    "snake.follow.off",
    "scroll.page_up",
    "scroll.page_down",
    "scroll.line_up",
    "scroll.line_down",
    "scroll.home",
    "scroll.end",
)


@dataclass(frozen=True)
class ChatControlConfig:
    mode: str = "interactive_safe"
    enabled: bool = True
    nl_mode_enabled: bool = False
    e2e_allowlist: tuple[str, ...] = _DEFAULT_E2E_ALLOWLIST
    confirmation_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.mode not in _VALID_MODES:
            raise ValueError(f"Invalid chat_control mode {self.mode!r}; valid: {sorted(_VALID_MODES)}")

    @property
    def is_autonomous_e2e(self) -> bool:
        return self.mode == "autonomous_e2e"


def load_chat_control_config(raw: dict | None = None) -> ChatControlConfig:
    kwargs: dict = {}
    if raw and isinstance(raw, dict):
        if "mode" in raw:
            kwargs["mode"] = str(raw["mode"])
        if "enabled" in raw:
            kwargs["enabled"] = bool(raw["enabled"])
        if "nl_mode_enabled" in raw:
            kwargs["nl_mode_enabled"] = bool(raw["nl_mode_enabled"])
        if "e2e_allowlist" in raw and isinstance(raw["e2e_allowlist"], list):
            kwargs["e2e_allowlist"] = tuple(str(x) for x in raw["e2e_allowlist"])
        if "confirmation_timeout_seconds" in raw:
            kwargs["confirmation_timeout_seconds"] = float(raw["confirmation_timeout_seconds"])

    env_mode = os.environ.get("ANANTA_TUI_CHAT_CONTROL_MODE", "").strip()
    if env_mode:
        kwargs["mode"] = env_mode if env_mode in _VALID_MODES else "interactive_safe"

    try:
        return ChatControlConfig(**kwargs)
    except ValueError:
        return ChatControlConfig()
