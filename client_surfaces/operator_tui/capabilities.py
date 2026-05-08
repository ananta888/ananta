from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class TerminalGraphicsCapability:
    protocol: str
    supported: bool
    reason: str


def detect_terminal_graphics(env: dict[str, str] | None = None) -> tuple[TerminalGraphicsCapability, ...]:
    values = env or os.environ
    term = values.get("TERM", "")
    term_program = values.get("TERM_PROGRAM", "")
    capabilities = [
        TerminalGraphicsCapability("kitty", bool(values.get("KITTY_WINDOW_ID")), "KITTY_WINDOW_ID present"),
        TerminalGraphicsCapability("iterm2", term_program == "iTerm.app", "TERM_PROGRAM=iTerm.app"),
        TerminalGraphicsCapability("sixel", "sixel" in term.lower(), "TERM contains sixel"),
    ]
    return tuple(
        capability
        if capability.supported
        else TerminalGraphicsCapability(capability.protocol, False, "not detected")
        for capability in capabilities
    )


def graphics_decision(env: dict[str, str] | None = None) -> dict[str, object]:
    capabilities = detect_terminal_graphics(env)
    supported = [item.protocol for item in capabilities if item.supported]
    return {
        "supported": bool(supported),
        "protocols": supported,
        "fallback": "text_diagram",
        "capabilities": [item.__dict__ for item in capabilities],
    }
