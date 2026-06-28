from __future__ import annotations

from typing import Any

from agent.services.notation_renderer_common import (
    NotationRenderError,
    _as_dict_entries,
    _as_list,
    _as_str,
    _check_identifier,
)

_VALID_ACTIVITY_SHAPES = {"action", "decision", "fork", "join", "initial",
                           "final", "merge"}


def _render_mermaid_activity(params: dict[str, Any]) -> tuple[str, str]:
    diagram_title = _as_str(params.get("diagram_title", "") or "", field="diagram_title")
    direction = _as_str(params.get("direction", "TB"), field="direction")
    if direction not in {"TB", "BT", "LR", "RL"}:
        raise NotationRenderError(
            f"direction must be one of TB/BT/LR/RL, got {direction!r}"
        )
    nodes_raw = _as_list(params.get("nodes"), field="nodes")
    if not nodes_raw:
        raise NotationRenderError("nodes must be a non-empty list")
    nodes = _as_dict_entries(nodes_raw, field="nodes")
    edges_raw = _as_list(params.get("edges"), field="edges")
    if not edges_raw:
        raise NotationRenderError("edges must be a non-empty list")
    edges = _as_dict_entries(edges_raw, field="edges")

    seen: set[str] = set()
    initial_count = 0
    final_count = 0
    for n in nodes:
        nid = _as_str(n.get("id"), field="nodes[].id")
        _check_identifier(nid, field="nodes[].id")
        if nid in seen:
            raise NotationRenderError(f"duplicate node id {nid!r}")
        seen.add(nid)
        shape = _as_str(n.get("shape"), field="nodes[].shape")
        if shape not in _VALID_ACTIVITY_SHAPES:
            raise NotationRenderError(
                f"node {nid!r} has unknown shape {shape!r}; expected one of "
                f"{sorted(_VALID_ACTIVITY_SHAPES)}"
            )
        if shape == "initial":
            initial_count += 1
        elif shape == "final":
            final_count += 1
    if initial_count != 1:
        raise NotationRenderError(
            f"activity must contain exactly one initial node, found "
            f"{initial_count}"
        )
    if final_count < 1:
        raise NotationRenderError(
            f"activity must contain at least one final node, found "
            f"{final_count}"
        )

    for e in edges:
        frm = _as_str(e.get("from"), field="edges[].from")
        to = _as_str(e.get("to"), field="edges[].to")
        if frm not in seen:
            raise NotationRenderError(
                f"edge from {frm!r} references unknown node"
            )
        if to not in seen:
            raise NotationRenderError(
                f"edge to {to!r} references unknown node"
            )

    def _shape_line(n: dict) -> str:
        nid = _as_str(n.get("id"), field="nodes[].id")
        label = _as_str(n.get("label", nid), field="nodes[].label")
        shape = _as_str(n.get("shape"), field="nodes[].shape")
        if shape in ("fork", "join"):
            return f'  {nid}(["{label}"])'
        if shape == "decision":
            return f'  {nid}{{"{label}"}}'
        if shape in ("initial", "final"):
            return f'  {nid}(((" ")))' if not label else f'  {nid}((("{label}")))'
        if shape == "merge":
            return f'  {nid}>"{label}"]'
        return f'  {nid}["{label}"]'

    lines: list[str] = [f"flowchart {direction}"]
    if diagram_title:
        lines.append(f"  %% {diagram_title}")
    for n in nodes:
        lines.append(_shape_line(n))
    for e in edges:
        frm = _as_str(e.get("from"), field="edges[].from")
        to = _as_str(e.get("to"), field="edges[].to")
        label = e.get("label")
        if isinstance(label, str) and label.strip():
            lines.append(f"  {frm} -->|{label.strip()}| {to}")
        else:
            lines.append(f"  {frm} --> {to}")
    return "\n".join(lines) + "\n", "diagram.mmd"


render_mermaid_activity = _render_mermaid_activity
