from __future__ import annotations

import argparse
import os
from collections.abc import Sequence

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
    parser.add_argument("--width", type=int, default=120)
    parser.add_argument("--height", type=int, default=32)
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
    if args.measure_first_paint:
        measurement = measure(
            "first_paint",
            budget.first_paint_ms,
            lambda: render_operator_shell(state, width=args.width, height=args.height),
        )
        state = state.with_updates(
            status_message=(
                f"{measurement.name}={measurement.elapsed_ms:.1f}ms budget={measurement.budget_ms:.1f}ms "
                f"ok={str(measurement.ok).lower()}"
            )
        )
    print(render_operator_shell(state, width=args.width, height=args.height))
    return 0
