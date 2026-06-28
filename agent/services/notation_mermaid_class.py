from __future__ import annotations

from typing import Any

from agent.services.notation_renderer_common import (
    NotationRenderError,
    _as_dict_entries,
    _as_list,
    _as_str,
    _check_identifier,
)

_ARROW_FOR_REL = {
    "inheritance": "<|--",
    "composition": "*--",
    "aggregation": "o--",
    "association": "-->",
    "realization": "..|>",
    "dependency": "..>",
    "link": "--",
}

_REL_LABEL_REQUIRED = set(_ARROW_FOR_REL.keys())


def _arrow(rel_type: str, *, from_label: str = "", to_label: str = "",
           label: str = "") -> str:
    arrow = _ARROW_FOR_REL.get(rel_type)
    if arrow is None:
        raise NotationRenderError(
            f"unknown relationship type {rel_type!r}; expected one of "
            f"{sorted(_ARROW_FOR_REL)}"
        )
    parts: list[str] = []
    if from_label:
        parts.append(f'"{from_label}"')
    parts.append(arrow)
    if to_label:
        parts.append(f'"{to_label}"')
    middle = " ".join(parts)
    if label:
        return f"{middle} : {label}"
    return middle


def _render_mermaid_class(params: dict[str, Any]) -> tuple[str, str]:
    diagram_title = _as_str(params.get("diagram_title", "") or "", field="diagram_title")
    direction = _as_str(params.get("direction", "TB"), field="direction")
    if direction not in {"TB", "BT", "LR", "RL"}:
        raise NotationRenderError(
            f"direction must be one of TB/BT/LR/RL, got {direction!r}"
        )
    classes_raw = _as_list(params.get("classes"), field="classes")
    if not classes_raw:
        raise NotationRenderError("classes must be a non-empty list")
    classes = _as_dict_entries(classes_raw, field="classes")
    relationships_raw = _as_list(params.get("relationships"), field="relationships")
    relationships = _as_dict_entries(relationships_raw, field="relationships")

    seen: set[str] = set()
    for c in classes:
        name = _as_str(c.get("name"), field="classes[].name")
        _check_identifier(name, field="classes[].name")
        if name in seen:
            raise NotationRenderError(f"duplicate class name {name!r}")
        seen.add(name)

    for r in relationships:
        rtype = _as_str(r.get("type"), field="relationships[].type")
        if rtype not in _ARROW_FOR_REL:
            raise NotationRenderError(
                f"relationship type {rtype!r} must be one of {sorted(_ARROW_FOR_REL)}"
            )
        frm = _as_str(r.get("from"), field="relationships[].from")
        to = _as_str(r.get("to"), field="relationships[].to")
        if frm not in seen:
            raise NotationRenderError(
                f"relationship from {frm!r} references unknown class"
            )
        if to not in seen:
            raise NotationRenderError(
                f"relationship to {to!r} references unknown class"
            )

    lines: list[str] = ["classDiagram"]
    if diagram_title:
        lines.append(f"%% {diagram_title}")
    lines.append(f"  direction {direction}")
    for c in classes:
        name = _as_str(c.get("name"), field="classes[].name")
        stereotype = c.get("stereotype")
        fields = _as_list(c.get("fields", []), field="classes[].fields")
        methods = _as_list(c.get("methods", []), field="classes[].methods")
        lines.append(f"  class {name} {{")
        if stereotype:
            lines.append(f"    <<{stereotype}>>")
        for f in fields:
            lines.append(f"    +{f}")
        for m in methods:
            if stereotype == "interface":
                lines.append(f"    {m} {{abstract}}")
            else:
                lines.append(f"    +{m}")
        lines.append("  }")
    for r in relationships:
        rtype = _as_str(r.get("type"), field="relationships[].type")
        frm = _as_str(r.get("from"), field="relationships[].from")
        to = _as_str(r.get("to"), field="relationships[].to")
        from_label = _as_str(r.get("from_label", ""), field="relationships[].from_label")
        to_label = _as_str(r.get("to_label", ""), field="relationships[].to_label")
        label = _as_str(r.get("label", ""), field="relationships[].label")
        arrow = _arrow(
            rtype,
            from_label=from_label,
            to_label=to_label,
            label=label,
        )
        lines.append(f"  {frm} {arrow} {to}")
    return "\n".join(lines) + "\n", "diagram.mmd"


render_mermaid_class = _render_mermaid_class
