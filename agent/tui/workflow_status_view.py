"""TUI workflow status renderer (WFG-022).

The TUI needs a compact, multi-line text view of the
workflow status returned by
``agent.services.workflow_status_service``. The view is
a pure function of the audit-query payload — it does not
talk to the hub directly. The TUI command
``:workflow status <goal_id>`` calls
``debug_workflow_status`` (WFG-017) and pipes the result
through ``render_workflow_status`` for ANSI colour.

Why a separate renderer and not a bare ``print()``?

  - The TUI shell colours the output when stdout is a TTY.
    The renderer applies the colour codes only when the
    caller passes ``colour=True`` so unit tests can assert
    on the plain text without ANSI escape sequences.
  - The TUI wants a stable width. The renderer pads the
    step list to a fixed column so the columns line up
    even when one step has a long role name.
  - The TUI shell can route the output to a notification
    surface (toast / banner) when the workflow has a
    blocking step. The renderer exposes
    ``has_blocking_step`` for that path.

The renderer does NOT import ``rich`` or ``blessed`` to
keep the TUI shell's surface dependency-free. ANSI codes
are hand-rolled (a 5-line subset is enough).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ANSI codes (used only when colour=True)
_RESET = "\x1b[0m"
_BOLD = "\x1b[1m"
_DIM = "\x1b[2m"
_RED = "\x1b[31m"
_YELLOW = "\x1b[33m"
_GREEN = "\x1b[32m"
_CYAN = "\x1b[36m"
_MAGENTA = "\x1b[35m"


@dataclass(frozen=True)
class WorkflowStatusView:
    """A pre-rendered workflow status view.

    The TUI shell can either ``print(view.text)`` directly
    or wrap it in a notification banner via
    ``view.summary_line``.
    """

    text: str
    summary_line: str
    has_blocking_step: bool
    blocking_step_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "summary_line": self.summary_line,
            "has_blocking_step": self.has_blocking_step,
            "blocking_step_count": self.blocking_step_count,
        }


def _truncate(value: str, max_len: int) -> str:
    if len(value) <= max_len:
        return value
    return value[: max_len - 1] + "\u2026"


def _colourize(text: str, code: str, *, enabled: bool) -> str:
    return f"{code}{text}{_RESET}" if enabled else text


def _status_marker(*, is_blocker: bool, gate: bool, gate_decision: str) -> str:
    """Pick the row prefix based on the step's state.

    The marker is the first column. Conventions:
      - ``[X]`` blocked step
      - ``[G]`` gate step (passed or not)
      - ``[ ]`` running / todo step
      - ``[+]`` completed step
    """
    if is_blocker:
        return "[X]"
    if gate and gate_decision == "passed":
        return "[+]"
    if gate:
        return "[G]"
    return "[ ]"


def render_workflow_status(
    payload: dict[str, Any], *, colour: bool = False, max_width: int = 80
) -> WorkflowStatusView:
    """Render the ``workflow_status.v1`` payload as TUI text.

    Parameters
    ----------
    payload:
        The dict returned by ``build_workflow_status`` (WFG-017).
    colour:
        Apply ANSI colour codes. The TUI shell sets this
        to True only when stdout is a TTY.
    max_width:
        The maximum width in characters. Defaults to 80
        (the TUI's default pane width). Used to truncate
        long role / reason text.
    """
    if not isinstance(payload, dict):
        return WorkflowStatusView(
            text="(no workflow status payload)",
            summary_line="(no workflow status payload)",
            has_blocking_step=False,
            blocking_step_count=0,
        )
    goal_id = str(payload.get("goal_id") or "").strip() or "?"
    plan_id = str(payload.get("plan_id") or "").strip()
    blueprint_id = str(payload.get("blueprint_id") or "").strip()
    steps = list(payload.get("steps") or [])
    handoffs = list(payload.get("handoff_events") or [])
    blocking = [s for s in steps if s.get("is_blocker")]
    header = _colourize(
        f"Workflow for goal {goal_id}", _BOLD, enabled=colour
    )
    lines: list[str] = [header]
    if plan_id:
        lines.append(f"  plan: {plan_id}")
    if blueprint_id:
        lines.append(
            f"  blueprint: {blueprint_id}@v{payload.get('blueprint_version', '')}"
        )
    lines.append(f"  steps: {len(steps)} ({len(blocking)} blocking)")
    if not steps:
        lines.append(_colourize("  (no workflow steps)", _DIM, enabled=colour))
    if steps:
        # Header row
        lines.append("")
        lines.append(_colourize("  STATUS  STEP                    ROLE                TASK   REASON", _DIM, enabled=colour))
        for step in steps:
            step_id = str(step.get("step_id") or "?")
            role = str(step.get("role") or "?")
            task_id = str(step.get("task_id") or "-")
            reason = (
                step.get("task_blocker_reason")
                or ",".join(step.get("blocked_reasons") or [])
                or "-"
            )
            gate = bool(step.get("gate", False))
            gate_decision = str(step.get("gate_decision") or "")
            marker = _status_marker(
                is_blocker=bool(step.get("is_blocker")),
                gate=gate,
                gate_decision=gate_decision,
            )
            colour_code = _RED if step.get("is_blocker") else (
                _MAGENTA if gate else _CYAN
            )
            lines.append(
                "  "
                + _colourize(marker, colour_code, enabled=colour)
                + "   "
                + _truncate(step_id, 24).ljust(24)
                + _truncate(role, 20).ljust(20)
                + _truncate(task_id, 8).ljust(8)
                + " " + _truncate(reason, max_width - 64)
            )
    if handoffs:
        lines.append("")
        lines.append(_colourize(f"  handoff events: {len(handoffs)}", _DIM, enabled=colour))
        for ev in handoffs[:10]:
            from_step = str(ev.get("from_step") or "-")
            to_step = str(ev.get("to_step") or "-")
            status = str(ev.get("status") or "-")
            lines.append(
                f"    {from_step} -> {to_step}  ({status})"
            )
        if len(handoffs) > 10:
            lines.append(f"    ... and {len(handoffs) - 10} more")
    text = "\n".join(lines)
    if blocking:
        summary = _colourize(
            f"workflow {goal_id}: {len(blocking)} blocking step(s)",
            _YELLOW,
            enabled=colour,
        )
    else:
        summary = _colourize(
            f"workflow {goal_id}: ok ({len(steps)} steps)",
            _GREEN,
            enabled=colour,
        )
    return WorkflowStatusView(
        text=text,
        summary_line=summary,
        has_blocking_step=bool(blocking),
        blocking_step_count=len(blocking),
    )


def render_workflow_status_text(
    payload: dict[str, Any], *, max_width: int = 80
) -> str:
    """Plain-text variant for tests, logs, and the TUI's
    ``:workflow status <goal_id>`` text-mode output.
    No ANSI codes. """
    return render_workflow_status(payload, colour=False, max_width=max_width).text
