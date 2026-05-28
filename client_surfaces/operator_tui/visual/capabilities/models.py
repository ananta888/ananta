from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TerminalVisualCapabilities:
    ansi: bool = True
    sixel: bool = False
    kitty_graphics: bool = False
    opengl_offscreen: bool = False

