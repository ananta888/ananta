from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Sequence

from agent.cli.splash import SplashMachine

from client_surfaces.operator_tui.adapters import SectionAdapterRegistry, merge_panel_state, merge_section_result
from client_surfaces.operator_tui.capabilities import graphics_decision
from client_surfaces.operator_tui.commands import execute_command
from client_surfaces.operator_tui.performance import PerformanceBudget, measure
from client_surfaces.operator_tui.models import FocusPane, OperatorMode, OperatorState
from client_surfaces.operator_tui.renderer import render_operator_shell
from client_surfaces.operator_tui.rollout import operator_tui_enabled, rollback_hint
from client_surfaces.operator_tui.sections import normalize_section_id
from client_surfaces.operator_tui.terminal import get_tty_size


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Ananta operator TUI shell.")
    parser.add_argument("--base-url", default=os.environ.get("ANANTA_BASE_URL", "http://localhost:5000"))
    parser.add_argument("--section", default="dashboard")
    parser.add_argument("--mode", choices=[mode.value for mode in OperatorMode], default=OperatorMode.NORMAL.value)
    parser.add_argument("--focus", choices=[pane.value for pane in FocusPane], default=FocusPane.NAVIGATION.value)
    parser.add_argument("--command", action="append", default=[])
    parser.add_argument("--show-help", action="store_true")
    parser.add_argument("--markdown-source", default="")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--measure-first-paint", action="store_true")
    parser.add_argument("--render-once", action="store_true")
    parser.add_argument("--width", type=int, default=120)
    parser.add_argument("--height", type=int, default=32)
    parser.add_argument(
        "--skip-splash",
        action="store_true",
        help="Skip the startup splash animation",
    )
    parser.add_argument("--splash-seconds", type=float, default=2.0, help="Duration of fullscreen splash")
    parser.add_argument("--no-3d", action="store_true", help="Disable 3D logo animation")
    parser.add_argument("--3d-preset", default=os.environ.get("ANANTA_TUI_3D_PRESET", "rotate_in"),
                        choices=["rotate_in", "snake_orbit", "depth_pulse"],
                        help="3D animation preset")
    parser.add_argument("--3d-fps", type=int, default=24, help="3D animation frame rate")
    parser.add_argument("--3d-duration-ms", type=int, default=2000, help="3D animation duration in ms")
    parser.add_argument("--splash-frame", default="",
                        help="Render a specific splash frame: 3d:0, 3d:mid, 3d:last, compact, pixel:intro")
    parser.add_argument(
        "--graphics",
        choices=["auto", "kitty", "sixel", "iterm2", "halfblock", "ascii", "none"],
        default=None,
        help="Terminal graphics backend selection.",
    )
    parser.add_argument(
        "--quality",
        choices=["low", "medium", "high", "ultra"],
        default=None,
        help="Rendering quality profile for logo/pixel path.",
    )
    parser.add_argument("--frame-width", type=int, default=None, help="Target frame width in pixels.")
    parser.add_argument("--frame-height", type=int, default=None, help="Target frame height in pixels.")
    parser.add_argument("--target-fps", type=int, default=None, help="Target FPS for animated pixel rendering.")
    parser.add_argument("--oversampling-factor", type=int, default=None, help="SVG oversampling factor (1..8).")
    parser.add_argument("--force-pixel-graphics", action="store_true", help="Avoid ASCII fallback when possible.")
    parser.add_argument(
        "--logo-renderer",
        choices=["auto", "ansi", "sixel", "kitty", "none"],
        default=None,
        help="Select persistent header logo renderer (default: auto).",
    )
    parser.add_argument(
        "--logo-animation",
        choices=["static", "pulse", "shimmer", "rotate_hint"],
        default=None,
        help="Set persistent header logo animation preset.",
    )
    parser.add_argument("--logo-fps", type=int, default=None, help="Set persistent header logo animation FPS.")
    parser.add_argument("--no-logo", action="store_true", help="Disable persistent header logo.")
    parser.add_argument("--enable-3d", action="store_true", help="Enable pixel-offscreen 3D header scene when possible.")
    parser.add_argument("--scene", default=None, help="3D scene id (e.g. demo-cube).")
    parser.add_argument(
        "--3d-renderer",
        choices=["auto", "moderngl", "raylib"],
        default=None,
        help="Preferred offscreen 3D renderer for header pixel path.",
    )
    return parser.parse_args(argv)


def _splash_disabled(args: argparse.Namespace) -> bool:
    splash_env = os.environ.get("ANANTA_TUI_SPLASH", "").strip().lower()
    if splash_env in {"0", "false", "no", "off"}:
        return True
    if splash_env in {"1", "true", "yes", "on"}:
        return False
    return bool(args.skip_splash)


def build_initial_state(args: argparse.Namespace) -> OperatorState:
    auth_state = "token" if os.environ.get("ANANTA_AUTH_TOKEN") else "session_env"
    if not os.environ.get("ANANTA_AUTH_TOKEN") and not os.environ.get("ANANTA_PASSWORD"):
        auth_state = "unset"
    return OperatorState(
        endpoint=str(args.base_url).rstrip("/"),
        auth_state=auth_state,
        mode=OperatorMode(args.mode),
        focus=FocusPane(args.focus),
        section_id=normalize_section_id(args.section),
        show_help=bool(args.show_help),
        markdown_source=args.markdown_source,
        terminal_graphics=graphics_decision(),
    )


def load_active_section(state: OperatorState, registry: SectionAdapterRegistry | None = None) -> OperatorState:
    adapters = registry or SectionAdapterRegistry()
    result = adapters.load(state.section_id)
    return state.with_updates(
        panel_states=merge_panel_state(state.panel_states, result),
        section_payloads=merge_section_result(state.section_payloads, result),
        status_message=result.message or state.status_message,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    _apply_logo_runtime_overrides(args)
    if not operator_tui_enabled():
        print(f"[OPERATOR-TUI-DISABLED] {rollback_hint()}")
        return 2
    if args.smoke:
        from client_surfaces.operator_tui.smoke import run_fixture_smoke

        result = run_fixture_smoke(args)
        print("operator_tui_smoke=ok" if result.ok else "operator_tui_smoke=failed")
        print("checks=" + ",".join(result.checks))
        print(result.output_preview)
        return 0 if result.ok else 1
    registry = SectionAdapterRegistry()
    budget = PerformanceBudget()
    state = load_active_section(build_initial_state(args), registry)
    for command in args.command:
        result = execute_command(command, state)
        state = load_active_section(result.state.with_updates(status_message=result.message), registry)

    splash: SplashMachine | None = None
    if _splash_disabled(args):
        splash = None
    else:
        from client_surfaces.operator_tui.animation3d.backends import BuiltinBackend
        from client_surfaces.operator_tui.animation3d.capabilities import detect_3d_capability
        from client_surfaces.operator_tui.animation3d.models import AnimationCapability

        cap = detect_3d_capability(
            terminal_width=args.width,
            terminal_height=args.height,
        )
        backend: BuiltinBackend | None = None
        opt_in_3d = not args.no_3d and cap.enabled
        if opt_in_3d:
            backend = BuiltinBackend()
        splash = SplashMachine(
            fullscreen_seconds=args.splash_seconds,
            animation_backend=backend,
            animation_capability=cap if backend is not None else None,
        )

    if args.measure_first_paint:
        measurement = measure(
            "first_paint",
            budget.first_paint_ms,
            lambda: render_operator_shell(state, width=args.width, height=args.height, splash=splash),
        )
        state = state.with_updates(
            status_message=(
                f"{measurement.name}={measurement.elapsed_ms:.1f}ms budget={measurement.budget_ms:.1f}ms "
                f"ok={str(measurement.ok).lower()}"
            )
        )
    if args.render_once and args.splash_frame:
        _handle_render_once(args, splash)
        return 0

    if not args.render_once and not args.command and not args.measure_first_paint and os.isatty(0):
        if splash is not None:
            _play_splash_to_terminal(state)
        from client_surfaces.operator_tui.interactive import InteractiveOperatorTui

        return InteractiveOperatorTui(state, registry, splash=None).run()
    if splash is not None:
        splash.tick()
    print(render_operator_shell(state, width=args.width, height=args.height, splash=splash))
    return 0


def _play_splash_to_terminal(state: OperatorState) -> None:
    from client_surfaces.operator_tui.splash_animation import build_splash_frames

    try:
        tty = open("/dev/tty", "w", encoding="utf-8", errors="replace")
    except OSError:
        return

    width, height = get_tty_size()
    height = min(height, 45)

    tty.write("\x1b[?25l\x1b[2J\x1b[H")
    tty.flush()

    frames = build_splash_frames(w=width, h=height, fps=24)
    _maybe_write_splash_debug(
        {
            "width": width,
            "height": height,
            "frames": len(frames),
            "mode": "splash_live",
        }
    )
    if not frames:
        tty.write("\x1b[?25h")
        tty.flush()
        tty.close()
        return

    interval = 1.0 / 24
    try:
        for frame in frames:
            tty.write(f"\x1b[H{frame}")
            tty.flush()
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        tty.write("\x1b[?25h\x1b[2J\x1b[H")
        tty.flush()
        tty.close()


def _handle_render_once(args: argparse.Namespace, splash: SplashMachine | None) -> None:
    from client_surfaces.operator_tui.animation3d.backends import BuiltinBackend
    from client_surfaces.operator_tui.animation3d.capabilities import detect_3d_capability

    frame = args.splash_frame
    width = args.width
    height = args.height

    if frame == "compact":
        from agent.cli.logo_layout import COMPACT_HEADER_LINES, render_compact_header
        from agent.cli.status_snapshot import StatusSnapshot

        header = render_compact_header(StatusSnapshot(), terminal_width=width, color=not args.no_3d)
        _maybe_write_splash_debug(
            {
                "width": width,
                "height": height,
                "frame": "compact",
                "mode": "render_once",
            }
        )
        print("\n".join(header[:COMPACT_HEADER_LINES]))
        return

    if frame == "pixel:intro":
        from client_surfaces.operator_tui.logo_renderer.animated_header import render_header_logo

        lines = render_header_logo(cols=max(20, width // 2), rows=8, color=not bool(os.environ.get("NO_COLOR")), t_now=0.0) or []
        _maybe_write_splash_debug(
            {
                "width": width,
                "height": height,
                "frame": frame,
                "mode": "render_once",
            }
        )
        print("\n".join(lines))
        return

    if frame.startswith("3d:"):
        if args.no_3d or os.environ.get("ANANTA_TUI_3D") == "0" or os.environ.get("ANANTA_TUI_SPLASH") == "0":
            print("")
            return
        cap = detect_3d_capability(
            terminal_width=width,
            terminal_height=height,
            is_tty=True,
            no_color=bool(os.environ.get("NO_COLOR")),
        )
        backend = BuiltinBackend()
        t_seconds = args.splash_seconds or 2.0
        if frame == "3d:0":
            t = 0.0
        elif frame == "3d:mid":
            t = t_seconds / 2.0
        elif frame == "3d:last":
            t = t_seconds
        else:
            try:
                t = float(frame.replace("3d:", "")) / cap.max_fps
            except (ValueError, TypeError):
                t = 0.0
        result = backend.frame_at(
            t=t,
            width=width,
            height=height,
            options={
                "preset": cap.preset_name,
                "color_mode": cap.color_mode,
                "no_color": cap.color_mode in ("mono", "plain_ascii"),
                "no_ansi": cap.color_mode == "plain_ascii",
            },
        )
        _maybe_write_splash_debug(
            {
                "width": width,
                "height": height,
                "frame": frame,
                "mode": "render_once",
            }
        )
        print(result.text)
        return

    print("")


def _apply_logo_runtime_overrides(args: argparse.Namespace) -> None:
    if bool(getattr(args, "no_logo", False)):
        os.environ["ANANTA_TUI_LOGO"] = "0"
    if getattr(args, "logo_renderer", None):
        os.environ["ANANTA_TUI_LOGO_RENDERER"] = str(args.logo_renderer)
    if getattr(args, "graphics", None):
        os.environ["ANANTA_TUI_GRAPHICS"] = str(args.graphics)
        # Keep legacy logo renderer in sync for existing selection flow.
        graphics = str(args.graphics)
        if graphics in {"kitty", "sixel", "none"}:
            os.environ["ANANTA_TUI_LOGO_RENDERER"] = graphics
        elif graphics in {"halfblock", "ascii", "iterm2"}:
            os.environ["ANANTA_TUI_LOGO_RENDERER"] = "ansi"
        elif graphics == "auto":
            os.environ["ANANTA_TUI_LOGO_RENDERER"] = "auto"
    if getattr(args, "quality", None):
        os.environ["ANANTA_TUI_LOGO_QUALITY"] = str(args.quality)
    if getattr(args, "frame_width", None) is not None:
        os.environ["ANANTA_TUI_FRAME_WIDTH"] = str(max(32, int(args.frame_width)))
    if getattr(args, "frame_height", None) is not None:
        os.environ["ANANTA_TUI_FRAME_HEIGHT"] = str(max(24, int(args.frame_height)))
    if getattr(args, "target_fps", None) is not None:
        os.environ["ANANTA_TUI_TARGET_FPS"] = str(max(1, min(60, int(args.target_fps))))
    if getattr(args, "oversampling_factor", None) is not None:
        os.environ["ANANTA_TUI_LOGO_OVERSAMPLING"] = str(max(1, min(8, int(args.oversampling_factor))))
    if bool(getattr(args, "force_pixel_graphics", False)):
        os.environ["ANANTA_TUI_FORCE_PIXEL_GRAPHICS"] = "1"
    if getattr(args, "logo_animation", None):
        os.environ["ANANTA_TUI_LOGO_ANIMATION"] = str(args.logo_animation)
    if getattr(args, "logo_fps", None) is not None:
        os.environ["ANANTA_TUI_LOGO_FPS"] = str(max(1, min(16, int(args.logo_fps))))
    if bool(getattr(args, "enable_3d", False)):
        os.environ["ANANTA_TUI_ENABLE_3D"] = "1"
    if getattr(args, "scene", None):
        os.environ["ANANTA_TUI_3D_SCENE"] = str(args.scene)
    if getattr(args, "3d_renderer", None):
        os.environ["ANANTA_TUI_3D_RENDERER"] = str(getattr(args, "3d_renderer"))

    # Render-once snapshots should be deterministic unless user explicitly opts in.
    if bool(getattr(args, "render_once", False)):
        if getattr(args, "logo_animation", None) is None and "ANANTA_TUI_LOGO_ANIMATION" not in os.environ:
            os.environ["ANANTA_TUI_LOGO_ANIMATION"] = "static"


def _maybe_write_splash_debug(payload: dict[str, object]) -> None:
    enabled = os.environ.get("ANANTA_TUI_SPLASH_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return
    path = os.environ.get("ANANTA_TUI_SPLASH_DEBUG_PATH", "/tmp/splash_debug.txt").strip() or "/tmp/splash_debug.txt"
    try:
        with open(path, "w", encoding="utf-8") as handle:
            for key in sorted(payload.keys()):
                handle.write(f"{key}={payload[key]}\n")
    except OSError:
        return
