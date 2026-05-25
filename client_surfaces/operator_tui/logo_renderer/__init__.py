from __future__ import annotations

from client_surfaces.operator_tui.logo_renderer.ansi_halfblock import (
    render_halfblock_image,
    render_halfblock_text,
)
from client_surfaces.operator_tui.logo_renderer.animated_header import render_ansi_header_logo, render_header_logo
from client_surfaces.operator_tui.logo_renderer.base import (
    LogoFrame,
    LogoRenderer,
    LogoRendererKind,
    LogoRendererProbe,
    LogoWriter,
)
from client_surfaces.operator_tui.logo_renderer.detect import detect_kitty_support, detect_sixel_support, resolve_renderer
from client_surfaces.operator_tui.logo_renderer.frame_cache import LogoFrameCache
from client_surfaces.operator_tui.logo_renderer.kitty import KittyRenderer
from client_surfaces.operator_tui.logo_renderer.sixel import SixelRenderer

__all__ = [
    "LogoFrame",
    "LogoRenderer",
    "LogoRendererKind",
    "LogoRendererProbe",
    "LogoWriter",
    "LogoFrameCache",
    "KittyRenderer",
    "SixelRenderer",
    "resolve_renderer",
    "detect_kitty_support",
    "detect_sixel_support",
    "render_ansi_header_logo",
    "render_header_logo",
    "render_halfblock_image",
    "render_halfblock_text",
]
