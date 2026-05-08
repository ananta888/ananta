from __future__ import annotations

import argparse
import os
from collections.abc import Sequence

from client_surfaces.operator_tui.commands import execute_command
from client_surfaces.operator_tui.models import FocusPane, OperatorMode, OperatorState
from client_surfaces.operator_tui.renderer import render_operator_shell
from client_surfaces.operator_tui.sections import normalize_section_id


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Ananta operator TUI shell.")
    parser.add_argument("--base-url", default=os.environ.get("ANANTA_BASE_URL", "http://localhost:5000"))
    parser.add_argument("--section", default="dashboard")
    parser.add_argument("--mode", choices=[mode.value for mode in OperatorMode], default=OperatorMode.NORMAL.value)
    parser.add_argument("--focus", choices=[pane.value for pane in FocusPane], default=FocusPane.NAVIGATION.value)
    parser.add_argument("--command", action="append", default=[])
    parser.add_argument("--show-help", action="store_true")
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
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    state = build_initial_state(args)
    for command in args.command:
        result = execute_command(command, state)
        state = result.state.with_updates(status_message=result.message)
    print(render_operator_shell(state, width=args.width, height=args.height))
    return 0
