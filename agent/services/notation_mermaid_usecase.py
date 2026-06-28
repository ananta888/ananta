from __future__ import annotations

from typing import Any

from agent.services.notation_renderer_common import (
    NotationRenderError,
    _as_dict_entries,
    _as_list,
    _as_str,
    _check_identifier,
)


def _render_mermaid_usecase(params: dict[str, Any]) -> tuple[str, str]:
    diagram_title = _as_str(params.get("diagram_title", "") or "", field="diagram_title")
    system_name = _as_str(params.get("system_name"), field="system_name")
    direction = _as_str(params.get("direction", "LR"), field="direction")
    if direction not in {"TB", "BT", "LR", "RL"}:
        raise NotationRenderError(
            f"direction must be one of TB/BT/LR/RL, got {direction!r}"
        )
    actors_raw = _as_list(params.get("actors"), field="actors")
    if not actors_raw:
        raise NotationRenderError("actors must be a non-empty list")
    actors = _as_dict_entries(actors_raw, field="actors")
    use_cases_raw = _as_list(params.get("use_cases"), field="use_cases")
    if not use_cases_raw:
        raise NotationRenderError("use_cases must be a non-empty list")
    use_cases = _as_dict_entries(use_cases_raw, field="use_cases")
    associations_raw = _as_list(params.get("associations"), field="associations")
    associations = _as_dict_entries(associations_raw, field="associations")
    includes_raw = _as_list(params.get("includes"), field="includes")
    includes = _as_dict_entries(includes_raw, field="includes")
    extends_raw = _as_list(params.get("extends"), field="extends")
    extends = _as_dict_entries(extends_raw, field="extends")

    actor_ids: set[str] = set()
    for a in actors:
        aid = _as_str(a.get("id"), field="actors[].id")
        _check_identifier(aid, field="actors[].id")
        if aid in actor_ids:
            raise NotationRenderError(f"duplicate actor id {aid!r}")
        actor_ids.add(aid)

    use_case_ids: set[str] = set()
    for uc in use_cases:
        uid = _as_str(uc.get("id"), field="use_cases[].id")
        _check_identifier(uid, field="use_cases[].id")
        if uid in use_case_ids:
            raise NotationRenderError(f"duplicate use-case id {uid!r}")
        use_case_ids.add(uid)

    for lnk in associations:
        if lnk.get("actor") not in actor_ids:
            raise NotationRenderError(
                f"association actor {lnk.get('actor')!r} references unknown actor"
            )
        if lnk.get("use_case") not in use_case_ids:
            raise NotationRenderError(
                f"association use_case {lnk.get('use_case')!r} references "
                f"unknown use-case"
            )
    for lnk in includes + extends:
        if lnk.get("from") not in use_case_ids:
            raise NotationRenderError(
                f"<<{'include' if lnk in includes else 'extend'}>> "
                f"from {lnk.get('from')!r} references unknown use-case"
            )
        if lnk.get("to") not in use_case_ids:
            raise NotationRenderError(
                f"<<{'include' if lnk in includes else 'extend'}>> "
                f"to {lnk.get('to')!r} references unknown use-case"
            )

    lines: list[str] = [f"flowchart {direction}"]
    if diagram_title:
        lines.append(f"  %% {diagram_title}")
    for a in actors:
        aid = _as_str(a.get("id"), field="actors[].id")
        label = _as_str(a.get("label", aid), field="actors[].label")
        lines.append(f'  {aid}(["{label}"])')
    lines.append(f"  subgraph {system_name}")
    for uc in use_cases:
        uid = _as_str(uc.get("id"), field="use_cases[].id")
        label = _as_str(uc.get("label", uid), field="use_cases[].label")
        lines.append(f'    {uid}[("{label}")]')
    lines.append("  end")
    for lnk in associations:
        actor = _as_str(lnk.get("actor"), field="associations[].actor")
        uc = _as_str(lnk.get("use_case"), field="associations[].use_case")
        lines.append(f"  {actor} --> {uc}")
    for lnk in includes:
        frm = _as_str(lnk.get("from"), field="includes[].from")
        to = _as_str(lnk.get("to"), field="includes[].to")
        lines.append(f"  {frm} -.-> {to} : <<include>>")
    for lnk in extends:
        frm = _as_str(lnk.get("from"), field="extends[].from")
        to = _as_str(lnk.get("to"), field="extends[].to")
        lines.append(f"  {frm} -.-> {to} : <<extend>>")
    return "\n".join(lines) + "\n", "diagram.mmd"


render_mermaid_usecase = _render_mermaid_usecase
