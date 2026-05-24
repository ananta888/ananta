from __future__ import annotations

import os

from client_surfaces.operator_tui.animation3d.models import AnimationCapability, REASON_CODES


def detect_3d_capability(
    env: dict[str, str] | None = None,
    terminal_width: int = 0,
    terminal_height: int = 0,
    *,
    no_color: bool | None = None,
    is_tty: bool | None = None,
) -> AnimationCapability:
    values = env or os.environ

    if terminal_width <= 0:
        try:
            terminal_width = os.get_terminal_size().columns
        except OSError:
            terminal_width = 80

    if terminal_height <= 0:
        try:
            terminal_height = os.get_terminal_size().lines
        except OSError:
            terminal_height = 24

    if no_color is None:
        no_color = bool(values.get("NO_COLOR"))

    if is_tty is None:
        try:
            is_tty = os.isatty(0) or os.isatty(1)
        except OSError:
            is_tty = False

    color_mode = "truecolor"
    if no_color:
        color_mode = "mono"

    splash_env = values.get("ANANTA_TUI_SPLASH", "").strip()
    if splash_env == "0":
        return AnimationCapability(
            enabled=False,
            reason_code="disabled_by_splash_env",
            terminal_width=terminal_width,
            terminal_height=terminal_height,
            color_mode=color_mode,
            preset_name="rotate_in",
            max_fps=0,
            duration_ms=0,
        )

    tui_3d_env = values.get("ANANTA_TUI_3D", "").strip()
    if tui_3d_env == "0":
        return AnimationCapability(
            enabled=False,
            reason_code="disabled_by_env",
            terminal_width=terminal_width,
            terminal_height=terminal_height,
            color_mode=color_mode,
            preset_name="rotate_in",
            max_fps=0,
            duration_ms=0,
        )

    if not is_tty:
        return AnimationCapability(
            enabled=False,
            reason_code="no_tty",
            terminal_width=terminal_width,
            terminal_height=terminal_height,
            color_mode=color_mode,
            preset_name="rotate_in",
            max_fps=0,
            duration_ms=0,
        )

    if terminal_width < 80 or terminal_height < 18:
        return AnimationCapability(
            enabled=False,
            reason_code="too_small",
            terminal_width=terminal_width,
            terminal_height=terminal_height,
            color_mode=color_mode,
            preset_name="rotate_in",
            max_fps=0,
            duration_ms=0,
        )

    if values.get("REDUCED_MOTION"):
        return AnimationCapability(
            enabled=False,
            reason_code="reduced_motion",
            terminal_width=terminal_width,
            terminal_height=terminal_height,
            color_mode=color_mode,
            preset_name="rotate_in",
            max_fps=0,
            duration_ms=0,
        )

    if no_color:
        color_mode = "mono"
    else:
        color_mode = "truecolor"

    tui_3d_preset = values.get("ANANTA_TUI_3D_PRESET", "rotate_in")
    if tui_3d_preset not in ("rotate_in", "snake_orbit", "depth_pulse"):
        tui_3d_preset = "rotate_in"

    try:
        fps = int(values.get("ANANTA_TUI_3D_FPS", "24"))
    except (ValueError, TypeError):
        fps = 24

    try:
        duration_ms = int(values.get("ANANTA_TUI_3D_DURATION_MS", "2000"))
    except (ValueError, TypeError):
        duration_ms = 2000

    return AnimationCapability(
        enabled=True,
        reason_code="ok",
        terminal_width=terminal_width,
        terminal_height=terminal_height,
        color_mode=color_mode,
        preset_name=tui_3d_preset,
        max_fps=max(1, min(60, fps)),
        duration_ms=max(500, min(10000, duration_ms)),
    )
