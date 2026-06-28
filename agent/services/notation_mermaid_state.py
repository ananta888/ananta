from __future__ import annotations

from typing import Any

from agent.services.notation_renderer_common import (
    NotationRenderError,
    _PSEUDOSTATE,
    _as_dict_entries,
    _as_list,
    _as_str,
    _check_identifier,
)


def _render_mermaid_state(params: dict[str, Any]) -> tuple[str, str]:
    diagram_title = _as_str(params.get("diagram_title", "") or "", field="diagram_title")
    direction = _as_str(params.get("direction", "TB"), field="direction")
    if direction not in {"TB", "BT", "LR", "RL"}:
        raise NotationRenderError(
            f"direction must be one of TB/BT/LR/RL, got {direction!r}"
        )
    states_raw = _as_list(params.get("states"), field="states")
    if not states_raw:
        raise NotationRenderError("states must be a non-empty list")
    states = _as_dict_entries(states_raw, field="states")
    transitions_raw = _as_list(params.get("transitions"), field="transitions")
    if not transitions_raw:
        raise NotationRenderError("transitions must be a non-empty list")
    transitions = _as_dict_entries(transitions_raw, field="transitions")

    seen_ids: set[str] = set()
    composite: dict[str, list[str]] = {}
    for s in states:
        sid = _as_str(s.get("id"), field="states[].id")
        _check_identifier(sid, field="states[].id")
        if sid in seen_ids:
            raise NotationRenderError(f"duplicate state id {sid!r}")
        seen_ids.add(sid)
        nested = _as_list(s.get("nested", []), field="states[].nested")
        if nested:
            for nid in nested:
                if not isinstance(nid, str):
                    raise NotationRenderError(
                        f"states[{sid!r}].nested entries must be strings"
                    )
            composite[sid] = list(nested)

    for sid, children in composite.items():
        for cid in children:
            if cid not in seen_ids:
                raise NotationRenderError(
                    f"composite state {sid!r} references unknown nested "
                    f"state {cid!r}"
                )

    seen_transitions: set[tuple[str, str]] = set()
    for t in transitions:
        frm = _as_str(t.get("from"), field="transitions[].from")
        to = _as_str(t.get("to"), field="transitions[].to")
        if frm != _PSEUDOSTATE:
            _check_identifier(frm, field="transitions[].from")
            if frm not in seen_ids:
                raise NotationRenderError(
                    f"transition from {frm!r} references unknown state"
                )
        if to != _PSEUDOSTATE:
            _check_identifier(to, field="transitions[].to")
            if to not in seen_ids:
                raise NotationRenderError(
                    f"transition to {to!r} references unknown state"
                )
        key = (frm, to)
        if key in seen_transitions:
            raise NotationRenderError(
                f"duplicate transition {frm!r} -> {to!r}"
            )
        seen_transitions.add(key)

    lines: list[str] = ["stateDiagram-v2"]
    if diagram_title:
        lines.append(f"  %% {diagram_title}")
    lines.append(f"  direction {direction}")
    for s in states:
        sid = _as_str(s.get("id"), field="states[].id")
        label = _as_str(s.get("label", sid), field="states[].label")
        if sid in composite:
            lines.append(f"  state {sid} {{")
            for cid in composite[sid]:
                lines.append(f"    {cid}")
            lines.append("  }")
        elif label != sid:
            lines.append(f'  state "{label}" as {sid}')
    for t in transitions:
        frm = _as_str(t.get("from"), field="transitions[].from")
        to = _as_str(t.get("to"), field="transitions[].to")
        event = t.get("event")
        guard = t.get("guard")
        parts: list[str] = []
        if isinstance(event, str) and event.strip():
            parts.append(event.strip())
        if isinstance(guard, str) and guard.strip():
            parts.append(f"[{guard.strip()}]")
        label = " : " + " ".join(parts) if parts else ""
        lines.append(f"  {frm} --> {to}{label}")
    return "\n".join(lines) + "\n", "diagram.mmd"
