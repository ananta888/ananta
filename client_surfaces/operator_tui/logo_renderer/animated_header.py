from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass

from client_surfaces.operator_tui.logo_renderer.animation_loop import AnimationLoop
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
_LOOPS: dict[int, AnimationLoop] = {}
_LAST_METRICS: dict[str, str | int | float | bool] = {}


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
    metrics_backend = "none"
    render_ms = 0.0
    encode_ms = 0.0
    output_ms = 0.0
    frame_w = 0
    frame_h = 0
    started = time.perf_counter()
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
        _record_metrics(
            env=env,
            backend="none",
            render_ms=0.0,
            encode_ms=0.0,
            output_ms=0.0,
            frame_width=0,
            frame_height=0,
        )
        return None

    use_3d = env.get("ANANTA_TUI_ENABLE_3D", "").strip().lower() in {"1", "true", "yes", "on"}
    if use_3d:
        scene_name = env.get("ANANTA_TUI_3D_SCENE", "demo-cube").strip().lower() or "demo-cube"
        renderer_pref = env.get("ANANTA_TUI_3D_RENDERER", "auto").strip().lower()
        renderer3d = _pick_3d_renderer(renderer_pref)
        cfg = SceneConfig(
            scene=scene_name,
            width_px=_frame_width(cols=cols, env=env),
            height_px=_frame_height(rows=rows, env=env),
            t=t_now or 0.0,
            quality=env.get("ANANTA_TUI_LOGO_QUALITY", "high"),
        )
        part_start = time.perf_counter()
        frame3d = renderer3d.render_scene(config=cfg)
        render_ms += (time.perf_counter() - part_start) * 1000.0
        frame_w = int(frame3d.width_px)
        frame_h = int(frame3d.height_px)
        frame3d = compose_text_overlay(
            frame3d,
            lines=[f"scene={scene_name}", f"renderer={renderer3d.name}"],
            x=6,
            y=6,
        )
        if not frame3d.is_empty and env.get("ANANTA_TUI_LOGO_STREAM_INLINE", "").strip().lower() in {"1", "true", "yes", "on"}:
            if decision.selected == "kitty":
                enc_start = time.perf_counter()
                seq = kitty_renderer.render_pixel_sequence(frame=frame3d, height_cells=rows)
                encode_ms += (time.perf_counter() - enc_start) * 1000.0
                if seq:
                    output_ms += (time.perf_counter() - started) * 1000.0
                    _record_metrics(
                        env=env,
                        backend="kitty-3d",
                        render_ms=render_ms,
                        encode_ms=encode_ms,
                        output_ms=output_ms,
                        frame_width=frame_w,
                        frame_height=frame_h,
                    )
                    return stream_frame_sequence(frame_sequence=seq, rows=rows, hide_cursor=True)
            if decision.selected == "sixel":
                enc_start = time.perf_counter()
                payload = sixel_renderer.render_pixel_frame(frame3d)
                encode_ms += (time.perf_counter() - enc_start) * 1000.0
                if payload:
                    seq = f"\x1b7\x1b[1;1H{payload}\x1b[{rows + 1};1H\x1b8"
                    output_ms += (time.perf_counter() - started) * 1000.0
                    _record_metrics(
                        env=env,
                        backend="sixel-3d",
                        render_ms=render_ms,
                        encode_ms=encode_ms,
                        output_ms=output_ms,
                        frame_width=frame_w,
                        frame_height=frame_h,
                    )
                    return stream_frame_sequence(frame_sequence=seq, rows=rows, hide_cursor=True)

    if decision.selected == "kitty":
        metrics_backend = "kitty"
        part_start = time.perf_counter()
        # Stream protocol output is implemented and testable in KittyRenderer, but
        # the current header composer is line-based; keep deterministic ANSI layout here.
        frame = kitty_renderer.render_frame(width_cells=cols, height_cells=rows, t=t_now or 0.0)
        render_ms += (time.perf_counter() - part_start) * 1000.0
        if frame.sequence and env.get("ANANTA_TUI_LOGO_STREAM_INLINE", "").strip().lower() in {"1", "true", "yes", "on"}:
            output_ms += (time.perf_counter() - started) * 1000.0
            _record_metrics(env=env, backend=metrics_backend, render_ms=render_ms, encode_ms=encode_ms, output_ms=output_ms, frame_width=frame_w, frame_height=frame_h)
            return stream_frame_sequence(frame_sequence=frame.sequence, rows=rows, hide_cursor=True)
        lines = render_ansi_header_logo(cols=cols, rows=rows, color=color, t_now=t_now)
        output_ms += (time.perf_counter() - started) * 1000.0
        _record_metrics(env=env, backend=metrics_backend, render_ms=render_ms, encode_ms=encode_ms, output_ms=output_ms, frame_width=frame_w, frame_height=frame_h)
        return lines

    if decision.selected == "sixel":
        metrics_backend = "sixel"
        part_start = time.perf_counter()
        frame = sixel_renderer.render_frame(width_cells=cols, height_cells=rows, t=t_now or 0.0)
        render_ms += (time.perf_counter() - part_start) * 1000.0
        if frame.sequence and env.get("ANANTA_TUI_LOGO_STREAM_INLINE", "").strip().lower() in {"1", "true", "yes", "on"}:
            output_ms += (time.perf_counter() - started) * 1000.0
            _record_metrics(env=env, backend=metrics_backend, render_ms=render_ms, encode_ms=encode_ms, output_ms=output_ms, frame_width=frame_w, frame_height=frame_h)
            return stream_frame_sequence(frame_sequence=frame.sequence, rows=rows, hide_cursor=True)
        lines = render_ansi_header_logo(cols=cols, rows=rows, color=color, t_now=t_now)
        output_ms += (time.perf_counter() - started) * 1000.0
        _record_metrics(env=env, backend=metrics_backend, render_ms=render_ms, encode_ms=encode_ms, output_ms=output_ms, frame_width=frame_w, frame_height=frame_h)
        return lines

    if decision.selected == "ansi":
        metrics_backend = "ansi"
        # Split text fallback between halfblock and ASCII.
        backend_name = select_graphics_backend(env=env, capabilities=detect_terminal_graphics_capabilities(env))
        if backend_name == "ascii":
            part_start = time.perf_counter()
            frame = AsciiRenderer().render_frame(width_cells=cols, height_cells=rows, t=t_now or 0.0)
            render_ms += (time.perf_counter() - part_start) * 1000.0
            output_ms += (time.perf_counter() - started) * 1000.0
            _record_metrics(env=env, backend="ascii", render_ms=render_ms, encode_ms=encode_ms, output_ms=output_ms, frame_width=frame_w, frame_height=frame_h)
            return list(frame.text_lines) if frame.text_lines else None
        part_start = time.perf_counter()
        frame = HalfblockRenderer().render_frame(width_cells=cols, height_cells=rows, t=t_now or 0.0)
        render_ms += (time.perf_counter() - part_start) * 1000.0
        if frame.text_lines:
            output_ms += (time.perf_counter() - started) * 1000.0
            _record_metrics(env=env, backend="halfblock", render_ms=render_ms, encode_ms=encode_ms, output_ms=output_ms, frame_width=frame_w, frame_height=frame_h)
            return list(frame.text_lines)

    lines = render_ansi_header_logo(cols=cols, rows=rows, color=color, t_now=t_now)
    output_ms += (time.perf_counter() - started) * 1000.0
    _record_metrics(env=env, backend=metrics_backend or "ansi", render_ms=render_ms, encode_ms=encode_ms, output_ms=output_ms, frame_width=frame_w, frame_height=frame_h)
    return lines


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


def _frame_width(*, cols: int, env: dict[str, str]) -> int:
    raw = env.get("ANANTA_TUI_FRAME_WIDTH", "").strip()
    if raw:
        try:
            return max(32, int(raw))
        except ValueError:
            pass
    return max(2, int(cols) * 8)


def _frame_height(*, rows: int, env: dict[str, str]) -> int:
    raw = env.get("ANANTA_TUI_FRAME_HEIGHT", "").strip()
    if raw:
        try:
            return max(24, int(raw))
        except ValueError:
            pass
    return max(2, int(rows) * 16)


def _record_metrics(
    *,
    env: dict[str, str],
    backend: str,
    render_ms: float,
    encode_ms: float,
    output_ms: float,
    frame_width: int,
    frame_height: int,
) -> None:
    target = max(1, min(60, int(env.get("ANANTA_TUI_TARGET_FPS", env.get("ANANTA_TUI_LOGO_FPS", "10")))))
    loop = _LOOPS.get(target)
    if loop is None:
        loop = AnimationLoop(target_fps=target, min_fps=5, max_fps=60)
        _LOOPS[target] = loop
    timing = loop.record_timing(render_ms=render_ms, encode_ms=encode_ms, output_ms=output_ms)
    _LAST_METRICS.clear()
    _LAST_METRICS.update(
        {
            "backend": backend,
            "render_ms": round(timing.render_ms, 3),
            "encode_ms": round(timing.encode_ms, 3),
            "output_ms": round(timing.output_ms, 3),
            "fps": int(loop.fps),
            "frame_w": int(frame_width),
            "frame_h": int(frame_height),
        }
    )
    if env.get("ANANTA_TUI_GFX_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}:
        print(
            (
                f"[tui-gfx] backend={backend} render_ms={timing.render_ms:.2f} "
                f"encode_ms={timing.encode_ms:.2f} output_ms={timing.output_ms:.2f} "
                f"fps={loop.fps} frame={int(frame_width)}x{int(frame_height)}"
            ),
            file=sys.stderr,
        )


def get_last_render_metrics() -> dict[str, str | int | float | bool]:
    return dict(_LAST_METRICS)
