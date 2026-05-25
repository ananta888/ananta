from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

RendererName = Literal["none", "ansi", "sixel", "kitty"]


@dataclass(frozen=True, slots=True)
class RendererDecision:
    requested: str
    selected: RendererName
    warning: str = ""


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
    if "kitty" in term:
        return True
    return term_program in {"wezterm", "ghostty", "kitty"}


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
