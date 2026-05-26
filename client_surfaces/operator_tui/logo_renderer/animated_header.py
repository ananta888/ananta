from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass

from client_surfaces.operator_tui.logo_renderer.detect import (
    detect_terminal_graphics_capabilities,
    is_debug_enabled,
    select_graphics_backend,
    resolve_renderer,
)
from client_surfaces.operator_tui.logo_renderer.ascii import AsciiRenderer
from client_surfaces.operator_tui.logo_renderer.compositor import compose_text_overlay
from client_surfaces.operator_tui.logo_renderer.frame_cache import LogoFrameCache
from client_surfaces.operator_tui.logo_renderer.halfblock import HalfblockRenderer
from client_surfaces.operator_tui.logo_renderer.kitty import KittyRenderer
from client_surfaces.operator_tui.logo_renderer.moderngl_renderer import ModernGLOffscreenRenderer
from client_surfaces.operator_tui.logo_renderer.raylib_renderer import RaylibPrototypeRenderer
from client_surfaces.operator_tui.logo_renderer.renderer_3d import SceneConfig
from client_surfaces.operator_tui.logo_renderer.sixel import SixelRenderer

_DEFAULT_SVG = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "ananta.svg"))
_CACHE = LogoFrameCache()


@dataclass(frozen=True, slots=True)
class HeaderLogoAnimationConfig:
    enabled: bool
    preset: str
    fps: int
    frame_count: int


def _parse_animation_config() -> HeaderLogoAnimationConfig:
    value = os.environ.get("ANANTA_TUI_LOGO_ANIMATION", "pulse").strip().lower()
    disabled = {"0", "false", "no", "off", "none"}
    if value in disabled:
        return HeaderLogoAnimationConfig(enabled=False, preset="static", fps=1, frame_count=1)

    preset = value or "pulse"
    if preset not in {"static", "pulse", "shimmer", "rotate_hint"}:
        preset = "pulse"

    try:
        fps = int(os.environ.get("ANANTA_TUI_LOGO_FPS", "6"))
    except (TypeError, ValueError):
        fps = 6
    fps = max(1, min(16, fps))

    if preset == "static":
        return HeaderLogoAnimationConfig(enabled=False, preset="static", fps=1, frame_count=1)

    frame_count = max(2, min(16, fps * 2))
    return HeaderLogoAnimationConfig(enabled=True, preset=preset, fps=fps, frame_count=frame_count)


def render_ansi_header_logo(
    *,
    cols: int,
    rows: int,
    color: bool,
    t_now: float | None = None,
) -> list[str] | None:
    config = _parse_animation_config()
    frames = _CACHE.get_ansi_frames(
        svg_path=_DEFAULT_SVG,
        width_cells=max(1, int(cols)),
        height_cells=max(1, int(rows)),
        renderer_mode="ansi",
        preset=config.preset,
        frame_count=config.frame_count,
        no_color=not color,
    )
    if not frames:
        return None

    if not config.enabled or len(frames) == 1:
        return frames[0]

    now = t_now if t_now is not None else time.monotonic()
    index = int(now * config.fps) % len(frames)
    return frames[index]


def stream_frame_sequence(*, frame_sequence: str, rows: int, hide_cursor: bool = True) -> list[str]:
    """Wrap stream-protocol frame output with safe cursor handling and row restore."""
    hide = "\x1b[?25l" if hide_cursor else ""
    show = "\x1b[?25h" if hide_cursor else ""
    sequence = f"\x1b7{hide}\x1b[1;1H{frame_sequence}\x1b[{max(1, int(rows)) + 1};1H{show}\x1b8"
    return [sequence] + ([""] * max(0, int(rows) - 1))


def render_header_logo(
    *,
    cols: int,
    rows: int,
    color: bool,
    t_now: float | None = None,
) -> list[str] | None:
    env = dict(os.environ)
    sixel_renderer = SixelRenderer()
    kitty_renderer = KittyRenderer()
    sixel_available = sixel_renderer.detect(
        probe=_build_probe(cols=cols, rows=rows, color=color, env=env)
    )
    kitty_available = kitty_renderer.detect(
        probe=_build_probe(cols=cols, rows=rows, color=color, env=env)
    )

    decision = resolve_renderer(env=env, sixel_available=sixel_available, kitty_available=kitty_available)
    if decision.warning and is_debug_enabled(env):
        print(f"[logo-renderer] {decision.warning}", file=sys.stderr)

    if decision.selected == "none":
        return None

    use_3d = env.get("ANANTA_TUI_ENABLE_3D", "").strip().lower() in {"1", "true", "yes", "on"}
    if use_3d:
        scene_name = env.get("ANANTA_TUI_3D_SCENE", "demo-cube").strip().lower() or "demo-cube"
        renderer_pref = env.get("ANANTA_TUI_3D_RENDERER", "auto").strip().lower()
        renderer3d = _pick_3d_renderer(renderer_pref)
        cfg = SceneConfig(scene=scene_name, width_px=max(2, cols * 8), height_px=max(2, rows * 16), t=t_now or 0.0)
        frame3d = renderer3d.render_scene(config=cfg)
        frame3d = compose_text_overlay(
            frame3d,
            lines=[f"scene={scene_name}", f"renderer={renderer3d.name}"],
            x=6,
            y=6,
        )
        if not frame3d.is_empty and env.get("ANANTA_TUI_LOGO_STREAM_INLINE", "").strip().lower() in {"1", "true", "yes", "on"}:
            if decision.selected == "kitty":
                seq = kitty_renderer.render_pixel_sequence(frame=frame3d, height_cells=rows)
                if seq:
                    return stream_frame_sequence(frame_sequence=seq, rows=rows, hide_cursor=True)
            if decision.selected == "sixel":
                payload = sixel_renderer.render_pixel_frame(frame3d)
                if payload:
                    seq = f"\x1b7\x1b[1;1H{payload}\x1b[{rows + 1};1H\x1b8"
                    return stream_frame_sequence(frame_sequence=seq, rows=rows, hide_cursor=True)

    if decision.selected == "kitty":
        # Stream protocol output is implemented and testable in KittyRenderer, but
        # the current header composer is line-based; keep deterministic ANSI layout here.
        frame = kitty_renderer.render_frame(width_cells=cols, height_cells=rows, t=t_now or 0.0)
        if frame.sequence and env.get("ANANTA_TUI_LOGO_STREAM_INLINE", "").strip().lower() in {"1", "true", "yes", "on"}:
            return stream_frame_sequence(frame_sequence=frame.sequence, rows=rows, hide_cursor=True)
        return render_ansi_header_logo(cols=cols, rows=rows, color=color, t_now=t_now)

    if decision.selected == "sixel":
        frame = sixel_renderer.render_frame(width_cells=cols, height_cells=rows, t=t_now or 0.0)
        if frame.sequence and env.get("ANANTA_TUI_LOGO_STREAM_INLINE", "").strip().lower() in {"1", "true", "yes", "on"}:
            return stream_frame_sequence(frame_sequence=frame.sequence, rows=rows, hide_cursor=True)
        return render_ansi_header_logo(cols=cols, rows=rows, color=color, t_now=t_now)

    if decision.selected == "ansi":
        # Split text fallback between halfblock and ASCII.
        backend_name = select_graphics_backend(env=env, capabilities=detect_terminal_graphics_capabilities(env))
        if backend_name == "ascii":
            frame = AsciiRenderer().render_frame(width_cells=cols, height_cells=rows, t=t_now or 0.0)
            return list(frame.text_lines) if frame.text_lines else None
        frame = HalfblockRenderer().render_frame(width_cells=cols, height_cells=rows, t=t_now or 0.0)
        if frame.text_lines:
            return list(frame.text_lines)

    return render_ansi_header_logo(cols=cols, rows=rows, color=color, t_now=t_now)


def _build_probe(*, cols: int, rows: int, color: bool, env: dict[str, str]):
    from client_surfaces.operator_tui.logo_renderer.base import LogoRendererProbe

    return LogoRendererProbe(
        term=env.get("TERM", ""),
        term_program=env.get("TERM_PROGRAM", ""),
        colorterm=env.get("COLORTERM", ""),
        no_color=not color,
        is_tty=True,
        width=max(1, int(cols)),
        height=max(1, int(rows)),
        env=env,
    )


def _pick_3d_renderer(renderer_pref: str):
    pref = (renderer_pref or "auto").strip().lower()
    if pref == "raylib":
        return RaylibPrototypeRenderer()
    if pref == "moderngl":
        return ModernGLOffscreenRenderer()
    # auto: prefer modern path, keep raylib as optional prototype fallback.
    mod = ModernGLOffscreenRenderer()
    if mod.is_available():
        return mod
    ray = RaylibPrototypeRenderer()
    if ray.is_available():
        return ray
    return mod
