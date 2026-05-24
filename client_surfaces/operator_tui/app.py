from __future__ import annotations

import argparse
import os
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
    parser.add_argument("--skip-splash", action="store_true", help="Skip the startup splash animation")
    parser.add_argument("--splash-seconds", type=float, default=2.0, help="Duration of fullscreen splash")
    parser.add_argument("--no-3d", action="store_true", help="Disable 3D logo animation")
    parser.add_argument("--3d-preset", default=os.environ.get("ANANTA_TUI_3D_PRESET", "rotate_in"),
                        choices=["rotate_in", "snake_orbit", "depth_pulse"],
                        help="3D animation preset")
    parser.add_argument("--3d-fps", type=int, default=24, help="3D animation frame rate")
    parser.add_argument("--3d-duration-ms", type=int, default=2000, help="3D animation duration in ms")
    parser.add_argument("--splash-frame", default="",
                        help="Render a specific splash frame: 3d:0, 3d:mid, 3d:last, compact")
    return parser.parse_args(argv)


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
    if args.skip_splash:
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
        env_3d = os.environ.get("ANANTA_TUI_3D", "").strip()
        opt_in_3d = env_3d in ("1", "yes", "on") or not args.no_3d and cap.enabled and os.environ.get("ANANTA_TUI_3D_PRESET")
        if opt_in_3d and cap.enabled:
            backend = BuiltinBackend()
        splash = SplashMachine(
            fullscreen_seconds=args.splash_seconds,
            animation_backend=backend,
            animation_capability=cap if backend is not None else None,
        )
        backend: BuiltinBackend | None = None
        if not args.no_3d and cap.enabled and (explicit_3d or args.splash_frame):
            backend = BuiltinBackend()
            cap = AnimationCapability(
                enabled=True, reason_code="ok",
                terminal_width=args.width, terminal_height=args.height,
                color_mode=cap.color_mode, preset_name=args.splash_frame.split(":")[0] if args.splash_frame and ":" in args.splash_frame else cap.preset_name,
                max_fps=cap.max_fps, duration_ms=cap.duration_ms,
            )

        splash = SplashMachine(
            fullscreen_seconds=args.splash_seconds,
            animation_backend=backend,
            animation_capability=cap,
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
        from client_surfaces.operator_tui.interactive import InteractiveOperatorTui

        return InteractiveOperatorTui(state, registry, splash=splash).run()
    if splash is not None:
        splash.tick()
    print(render_operator_shell(state, width=args.width, height=args.height, splash=splash))
    return 0


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
        print("\n".join(header[:COMPACT_HEADER_LINES]))
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
        print(result.text)
        return

    print("")
