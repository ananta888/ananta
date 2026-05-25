from __future__ import annotations

import os
import time
from dataclasses import dataclass

from client_surfaces.operator_tui.logo_renderer.frame_cache import LogoFrameCache

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
    if preset not in {"static", "pulse", "shimmer"}:
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
