from __future__ import annotations

from dataclasses import dataclass

from client_surfaces.operator_tui.app import build_initial_state, load_active_section
from client_surfaces.operator_tui.commands import execute_command
from client_surfaces.operator_tui.renderer import render_operator_shell


@dataclass(frozen=True)
class SmokeResult:
    ok: bool
    checks: tuple[str, ...]
    output_preview: str


def run_fixture_smoke(args) -> SmokeResult:
    state = load_active_section(build_initial_state(args))
    checks = ["launch", "first_paint"]
    for command in (":section tasks", ":refresh", ":section help"):
        result = execute_command(command, state)
        state = load_active_section(result.state)
        checks.append(f"command:{command}")
    output = render_operator_shell(state, width=100, height=24)
    normalized = output.lower()
    ok = "ananta" in normalized and ("help" in normalized or "commands:" in normalized)
    return SmokeResult(ok=ok, checks=tuple(checks), output_preview="\n".join(output.splitlines()[:12]))
