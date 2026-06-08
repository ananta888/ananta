"""Mermaid Export + TUI read-only view (VPAD-009).

Converts a VisualProcessGraph to a Mermaid flowchart diagram string.
Also provides a plain-text ASCII summary for the TUI.
"""
from __future__ import annotations

import textwrap

from agent.visual_process.models import VisualProcessEdge, VisualProcessGraph, VisualProcessStep
from agent.visual_process.policy_hints import HINT_REQUIRES_APPROVAL, HINT_HIGH_RISK, classify_step


def to_mermaid(graph: VisualProcessGraph, *, direction: str = "LR") -> str:
    """Return a Mermaid flowchart string.

    direction: LR (left-right) | TD (top-down)
    """
    lines = [f"flowchart {direction}"]

    for step in graph.steps:
        hints = set(classify_step(step))
        label = step.label.replace('"', "'")
        shape_open, shape_close = _shape(step, hints)
        lines.append(f'    {step.id}{shape_open}"{label}"{shape_close}')

    for edge in graph.edges:
        arrow = _arrow(edge)
        label_part = f'|"{edge.label}"|' if edge.label else ""
        lines.append(f"    {edge.source} {arrow}{label_part} {edge.target}")

    # Style high-risk and gate steps
    for step in graph.steps:
        hints = set(classify_step(step))
        if HINT_HIGH_RISK in hints or HINT_REQUIRES_APPROVAL in hints:
            lines.append(f"    style {step.id} fill:#ff6b6b,color:#fff")
        elif "read_only" in hints:
            lines.append(f"    style {step.id} fill:#74b9ff,color:#fff")

    return "\n".join(lines)


def to_tui_text(graph: VisualProcessGraph) -> str:
    """Return a compact ASCII representation for the TUI read-only view."""
    lines = [
        f"╔═ {graph.name} (v{graph.version}) {'═' * max(0, 50 - len(graph.name))}",
        f"║  {graph.description}" if graph.description else "",
        f"║  Steps: {len(graph.steps)}  Edges: {len(graph.edges)}  Tags: {', '.join(graph.tags) or '—'}",
        "╠" + "═" * 56,
    ]
    for step in graph.steps:
        hints = classify_step(step)
        flag = "🔒" if "requires_approval" in hints else ("⚠" if "high_risk" in hints else " ")
        io_in = ", ".join(step.io.input_names()) or "—"
        io_out = ", ".join(step.io.output_names()) or "—"
        lines.append(f"║ {flag} [{step.kind:12}] {step.label}")
        lines.append(f"║      in: {io_in}  →  out: {io_out}")
        if step.agent_skill_profile_id:
            lines.append(f"║      profile: {step.agent_skill_profile_id}")

    lines.append("╠" + "═" * 56)
    for edge in graph.edges:
        src_step = graph.step_by_id(edge.source)
        tgt_step = graph.step_by_id(edge.target)
        src_lbl = src_step.label if src_step else edge.source
        tgt_lbl = tgt_step.label if tgt_step else edge.target
        cond = edge.condition.kind
        if edge.is_back_edge():
            lp = edge.condition.loop_policy
            iter_str = f" ×{lp.max_iterations}" if lp else ""
            lines.append(f"║ ↩ {src_lbl} → {tgt_lbl}  [loop{iter_str}]")
        else:
            label_str = f" ({edge.label})" if edge.label else ""
            lines.append(f"║ → {src_lbl} → {tgt_lbl}  [{cond}]{label_str}")

    lines.append("╚" + "═" * 56)
    return "\n".join(l for l in lines if l is not None)


# ── helpers ───────────────────────────────────────────────────────────────────

def _shape(step: VisualProcessStep, hints: set[str]) -> tuple[str, str]:
    if step.gate or HINT_REQUIRES_APPROVAL in hints:
        return "{", "}"        # hexagon — decision / gate
    if "read_only" in hints:
        return "([", "])"      # stadium — read-only / query
    return "[", "]"            # rectangle — default


def _arrow(edge: VisualProcessEdge) -> str:
    k = edge.condition.kind
    if k == "back_edge":
        return "-..->"
    if k == "on_success":
        return "-- ✓ -->"
    if k == "on_failure":
        return "-- ✗ -->"
    if k == "on_output":
        return "-- out -->"
    return "-->"
