from __future__ import annotations

from client_surfaces.operator_tui.logo_renderer.ansi_halfblock import (
    render_halfblock_image,
    render_halfblock_text,
)
from client_surfaces.operator_tui.logo_renderer.animation_loop import AnimationLoop, FrameTiming
from client_surfaces.operator_tui.logo_renderer.animated_header import render_ansi_header_logo, render_header_logo
from client_surfaces.operator_tui.logo_renderer.base import (
    LogoFrame,
    LogoRenderer,
    LogoRendererKind,
    LogoRendererProbe,
    LogoWriter,
    TerminalGraphicsBackend,
)
from client_surfaces.operator_tui.logo_renderer.detect import (
    detect_kitty_support,
    detect_sixel_support,
    detect_terminal_graphics_capabilities,
    select_graphics_backend,
    resolve_renderer,
)
from client_surfaces.operator_tui.logo_renderer.compositor import compose_overlay, compose_text_overlay
from client_surfaces.operator_tui.logo_renderer.frame import PixelFrame, frame_from_svg
from client_surfaces.operator_tui.logo_renderer.frame_cache import LogoFrameCache
from client_surfaces.operator_tui.logo_renderer.halfblock import HalfblockRenderer
from client_surfaces.operator_tui.logo_renderer.kitty import KittyRenderer
from client_surfaces.operator_tui.logo_renderer.moderngl_renderer import ModernGLOffscreenRenderer
from client_surfaces.operator_tui.logo_renderer.ascii import AsciiRenderer
from client_surfaces.operator_tui.logo_renderer.pixel_geometry import (
    fit_frame_size_to_terminal,
    header_logo_target_pixels,
    map_cells_to_pixels,
    terminal_cell_pixels,
)
from client_surfaces.operator_tui.logo_renderer.raylib_renderer import RaylibPrototypeRenderer
from client_surfaces.operator_tui.logo_renderer.renderer_3d import Offscreen3DRenderer, SceneConfig
from client_surfaces.operator_tui.logo_renderer.snake_motion import PixelPoint, pixel_boost_speed, smooth_follow
from client_surfaces.operator_tui.logo_renderer.sixel import SixelRenderer

__all__ = [
    "AnimationLoop",
    "FrameTiming",
    "LogoFrame",
    "LogoRenderer",
    "LogoRendererKind",
    "LogoRendererProbe",
    "LogoWriter",
    "TerminalGraphicsBackend",
    "PixelFrame",
    "frame_from_svg",
    "Offscreen3DRenderer",
    "SceneConfig",
    "LogoFrameCache",
    "KittyRenderer",
    "SixelRenderer",
    "HalfblockRenderer",
    "AsciiRenderer",
    "ModernGLOffscreenRenderer",
    "RaylibPrototypeRenderer",
    "compose_overlay",
    "compose_text_overlay",
    "PixelPoint",
    "smooth_follow",
    "pixel_boost_speed",
    "resolve_renderer",
    "detect_kitty_support",
    "detect_sixel_support",
    "detect_terminal_graphics_capabilities",
    "select_graphics_backend",
    "terminal_cell_pixels",
    "map_cells_to_pixels",
    "fit_frame_size_to_terminal",
    "header_logo_target_pixels",
    "render_ansi_header_logo",
    "render_header_logo",
    "render_halfblock_image",
    "render_halfblock_text",
]
