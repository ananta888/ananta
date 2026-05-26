from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

RendererName = Literal["none", "ansi", "sixel", "kitty"]
GraphicsBackendName = Literal["kitty", "sixel", "iterm2", "halfblock", "ascii"]


@dataclass(frozen=True, slots=True)
class RendererDecision:
    requested: str
    selected: RendererName
    warning: str = ""


@dataclass(frozen=True, slots=True)
class GraphicsCapabilities:
    kitty: bool
    sixel: bool
    iterm2_inline: bool
    truecolor: bool
    source_term: str
    source_program: str


def is_debug_enabled(env: dict[str, str] | None = None) -> bool:
    values = env or os.environ
    return (values.get("ANANTA_TUI_LOGO_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}) or (
        values.get("ANANTA_TUI_VERBOSE", "").strip().lower() in {"1", "true", "yes", "on"}
    )


def detect_sixel_support(env: dict[str, str] | None = None) -> bool:
    values = env or os.environ
    term = values.get("TERM", "").lower()
    term_program = values.get("TERM_PROGRAM", "").lower()
    if values.get("ANANTA_TUI_DISABLE_SIXEL", "").strip().lower() in {"1", "true", "yes", "on"}:
        return False
    if values.get("ANANTA_TUI_ENABLE_SIXEL", "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    if "sixel" in term:
        return True
    if values.get("WT_SESSION", "").strip():
        return True
    return term_program in {"wezterm", "mintty", "contour"}


def detect_kitty_support(env: dict[str, str] | None = None) -> bool:
    values = env or os.environ
    term = values.get("TERM", "").lower()
    term_program = values.get("TERM_PROGRAM", "").lower()
    if values.get("ANANTA_TUI_DISABLE_KITTY", "").strip().lower() in {"1", "true", "yes", "on"}:
        return False
    if values.get("ANANTA_TUI_ENABLE_KITTY", "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    if values.get("KITTY_WINDOW_ID"):
        return True
    if values.get("WEZTERM_EXECUTABLE", "").strip():
        return True
    if "kitty" in term:
        return True
    return term_program in {"wezterm", "ghostty", "kitty"}


def detect_iterm2_support(env: dict[str, str] | None = None) -> bool:
    values = env or os.environ
    term_program = values.get("TERM_PROGRAM", "").lower()
    return term_program == "iterm.app"


def detect_terminal_graphics_capabilities(env: dict[str, str] | None = None) -> GraphicsCapabilities:
    values = env or os.environ
    no_color = values.get("NO_COLOR", "").strip().lower() in {"1", "true", "yes", "on"}
    colorterm = values.get("COLORTERM", "").lower()
    term = values.get("TERM", "").lower()
    truecolor = (("truecolor" in colorterm or "24bit" in colorterm or term.endswith("-direct")) and not no_color)
    return GraphicsCapabilities(
        kitty=detect_kitty_support(values),
        sixel=detect_sixel_support(values),
        iterm2_inline=detect_iterm2_support(values),
        truecolor=truecolor,
        source_term=values.get("TERM", ""),
        source_program=values.get("TERM_PROGRAM", ""),
    )


def select_graphics_backend(
    *,
    env: dict[str, str] | None = None,
    capabilities: GraphicsCapabilities | None = None,
) -> GraphicsBackendName:
    values = env or os.environ
    forced = (values.get("ANANTA_TUI_GRAPHICS", "").strip().lower() or values.get("ANANTA_TUI_LOGO_RENDERER", "").strip().lower())
    if forced in {"kitty", "sixel", "iterm2", "halfblock", "ascii"}:
        return forced  # explicit override wins
    if forced == "ansi":
        return "halfblock"
    if forced == "none":
        return "ascii"

    caps = capabilities or detect_terminal_graphics_capabilities(values)
    if caps.kitty:
        return "kitty"
    if caps.sixel:
        return "sixel"
    if caps.iterm2_inline:
        return "iterm2"
    if caps.truecolor:
        return "halfblock"
    return "ascii"


def resolve_renderer(
    *,
    env: dict[str, str] | None = None,
    sixel_available: bool,
    kitty_available: bool,
) -> RendererDecision:
    values = env or os.environ
    logo_enabled = values.get("ANANTA_TUI_LOGO", "1").strip().lower() not in {"0", "false", "no", "off"}
    if not logo_enabled:
        return RendererDecision(requested="none", selected="none")

    requested = values.get("ANANTA_TUI_LOGO_RENDERER", "auto").strip().lower() or "auto"
    if requested == "none":
        return RendererDecision(requested=requested, selected="none")
    if requested == "ansi":
        return RendererDecision(requested=requested, selected="ansi")
    if requested == "sixel":
        if sixel_available:
            return RendererDecision(requested=requested, selected="sixel")
        return RendererDecision(
            requested=requested,
            selected="ansi",
            warning="Requested sixel renderer unavailable; using ansi fallback.",
        )
    if requested == "kitty":
        if kitty_available:
            return RendererDecision(requested=requested, selected="kitty")
        return RendererDecision(
            requested=requested,
            selected="ansi",
            warning="Requested kitty renderer unavailable; using ansi fallback.",
        )

    # unknown/manual garbage should not crash; fallback to auto
    auto_order: list[RendererName] = ["kitty", "sixel", "ansi"]
    for item in auto_order:
        if item == "kitty" and kitty_available:
            return RendererDecision(requested=requested, selected="kitty")
        if item == "sixel" and sixel_available:
            return RendererDecision(requested=requested, selected="sixel")
        if item == "ansi":
            return RendererDecision(requested=requested, selected="ansi")
    return RendererDecision(requested=requested, selected="ansi")
