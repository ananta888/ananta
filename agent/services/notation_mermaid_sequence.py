from __future__ import annotations

from typing import Any

from agent.services.notation_renderer_common import (
    NotationRenderError,
    _as_bool,
    _as_dict_entries,
    _as_list,
    _as_str,
    _check_identifier,
)


def _render_mermaid_sequence(params: dict[str, Any]) -> tuple[str, str]:
    diagram_title = _as_str(params.get("diagram_title", "") or "", field="diagram_title")
    autonumber = _as_bool(params.get("autonumber"), default=False)
    participants_raw = _as_list(params.get("participants"), field="participants")
    if not participants_raw:
        raise NotationRenderError("participants must be a non-empty list")
    participants = _as_dict_entries(participants_raw, field="participants")
    messages_raw = _as_list(params.get("messages"), field="messages")
    if not messages_raw:
        raise NotationRenderError("messages must be a non-empty list")
    messages = _as_dict_entries(messages_raw, field="messages")
    fragments_raw = _as_list(params.get("fragments"), field="fragments")
    fragments = _as_dict_entries(fragments_raw, field="fragments")

    valid_kinds = {"participant", "actor", "boundary", "control", "entity", "database"}
    seen_ids: set[str] = set()
    participant_kinds: dict[str, str] = {}
    for p in participants:
        pid = _as_str(p.get("id"), field="participants[].id")
        _check_identifier(pid, field="participants[].id")
        if pid in seen_ids:
            raise NotationRenderError(f"duplicate participant id {pid!r}")
        seen_ids.add(pid)
        kind = _as_str(p.get("kind", "participant"), field="participants[].kind")
        if kind not in valid_kinds:
            raise NotationRenderError(
                f"participant {pid!r} has unknown kind {kind!r}; "
                f"expected one of {sorted(valid_kinds)}"
            )
        participant_kinds[pid] = kind

    for m in messages:
        frm = _as_str(m.get("from"), field="messages[].from")
        to = _as_str(m.get("to"), field="messages[].to")
        if frm not in seen_ids:
            raise NotationRenderError(
                f"message from {frm!r} references unknown participant"
            )
        if to not in seen_ids:
            raise NotationRenderError(
                f"message to {to!r} references unknown participant"
            )
        if not _as_str(m.get("text"), field="messages[].text"):
            raise NotationRenderError("message text must be a non-empty string")

    valid_fragment_types = {"alt", "par", "loop", "opt", "critical"}

    def _validate_fragment_tree(frag_list: list[dict], *, path: str) -> None:
        for idx, frag in enumerate(frag_list):
            ftype = _as_str(frag.get("type"), field=f"{path}[{idx}].type")
            if ftype not in valid_fragment_types:
                raise NotationRenderError(
                    f"{path}[{idx}].type {ftype!r} must be one of "
                    f"{sorted(valid_fragment_types)}"
                )
            label = frag.get("label")
            if not isinstance(label, str) or not label.strip():
                raise NotationRenderError(
                    f"{path}[{idx}].label must be a non-empty string"
                )
            if ftype == "alt":
                branches = frag.get("branches")
                if not isinstance(branches, list) or not branches:
                    raise NotationRenderError(
                        f"{path}[{idx}].branches must be a non-empty list"
                    )
                for bidx, branch in enumerate(branches):
                    if not isinstance(branch, dict):
                        raise NotationRenderError(
                            f"{path}[{idx}].branches[{bidx}] must be a dict"
                        )
                    cond = branch.get("condition")
                    if not isinstance(cond, str) or not cond.strip():
                        raise NotationRenderError(
                            f"{path}[{idx}].branches[{bidx}].condition must "
                            f"be a non-empty string"
                        )
                    nested_msgs = _as_list(
                        branch.get("messages", []),
                        field=f"{path}[{idx}].branches[{bidx}].messages",
                    )
                    for nested in nested_msgs:
                        if not isinstance(nested, dict):
                            raise NotationRenderError(
                                f"{path}[{idx}].branches[{bidx}].messages "
                                f"must be a list of dicts"
                            )
                        frm = nested.get("from")
                        to = nested.get("to")
                        if frm not in seen_ids:
                            raise NotationRenderError(
                                f"alt branch message from {frm!r} references "
                                f"unknown participant"
                            )
                        if to not in seen_ids:
                            raise NotationRenderError(
                                f"alt branch message to {to!r} references "
                                f"unknown participant"
                            )
            else:
                nested_msgs = _as_list(frag.get("messages", []), field=f"{path}[{idx}].messages")
                for nested in nested_msgs:
                    if not isinstance(nested, dict):
                        raise NotationRenderError(
                            f"{path}[{idx}].messages must be a list of dicts"
                        )
                    frm = nested.get("from")
                    to = nested.get("to")
                    if frm not in seen_ids:
                        raise NotationRenderError(
                            f"fragment message from {frm!r} references "
                            f"unknown participant"
                        )
                    if to not in seen_ids:
                        raise NotationRenderError(
                            f"fragment message to {to!r} references "
                            f"unknown participant"
                        )

    _validate_fragment_tree(fragments, path="fragments")

    def _arrow_for(msg_type: str) -> str:
        if msg_type == "async":
            return "--)"
        if msg_type == "return":
            return "-->>"
        return "->>"

    lines: list[str] = ["sequenceDiagram"]
    if diagram_title:
        lines.append(f"  %% {diagram_title}")
    if autonumber:
        lines.append("  autonumber")
    for p in participants:
        pid = _as_str(p.get("id"), field="participants[].id")
        label = _as_str(p.get("label", pid), field="participants[].label")
        kind = participant_kinds[pid]
        keyword = "actor" if kind == "actor" else "participant"
        if label == pid:
            lines.append(f"  {keyword} {pid}")
        else:
            lines.append(f"  {keyword} {pid} as {label}")
    for m in messages:
        frm = _as_str(m.get("from"), field="messages[].from")
        to = _as_str(m.get("to"), field="messages[].to")
        text = _as_str(m.get("text"), field="messages[].text")
        msg_type = _as_str(m.get("type", "sync"), field="messages[].type")
        arrow = _arrow_for(msg_type)
        lines.append(f"  {frm}{arrow}{to}: {text}")
        if _as_bool(m.get("activate"), default=False):
            lines.append(f"  activate {to}")
            lines.append(f"  deactivate {to}")

    def _emit_fragment(frag_list: list[dict], indent: int = 2) -> None:
        pad = "  " * indent
        for frag in frag_list:
            ftype = _as_str(frag.get("type"), field="fragments[].type")
            label = _as_str(frag.get("label"), field="fragments[].label")
            condition = frag.get("condition")
            if ftype == "alt":
                branches_list = _as_list(
                    frag.get("branches", []), field="fragments.branches"
                )
                first_cond = _as_str(
                    _as_dict_entries(branches_list,
                                     field="fragments.branches")[0].get("condition"),
                    field="fragments.branches[0].condition",
                )
                lines.append(f"{pad}alt {label}")
                lines.append(f"{pad}  {first_cond}")
                for nested in _as_dict_entries(
                    frag.get("branches", [{}])[0].get("messages", []),
                    field="fragments.branches[0].messages",
                ):
                    frm = _as_str(nested.get("from"), field="fragments.branches[].messages.from")
                    to = _as_str(nested.get("to"), field="fragments.branches[].messages.to")
                    ntext = _as_str(nested.get("text"), field="fragments.branches[].messages.text")
                    ntype = _as_str(nested.get("type", "sync"), field="fragments.branches[].messages.type")
                    lines.append(f"{pad}    {frm}{_arrow_for(ntype)}{to}: {ntext}")
                for branch in _as_dict_entries(frag.get("branches", []), field="fragments.branches")[1:]:
                    cond = _as_str(branch.get("condition"), field="fragments.branches[].condition")
                    lines.append(f"{pad}else {cond}")
                    for nested in _as_dict_entries(
                        branch.get("messages", []),
                        field="fragments.branches[].messages",
                    ):
                        frm = _as_str(nested.get("from"), field="fragments.branches[].messages.from")
                        to = _as_str(nested.get("to"), field="fragments.branches[].messages.to")
                        ntext = _as_str(nested.get("text"), field="fragments.branches[].messages.text")
                        ntype = _as_str(nested.get("type", "sync"), field="fragments.branches[].messages.type")
                        lines.append(f"{pad}    {frm}{_arrow_for(ntype)}{to}: {ntext}")
                lines.append(f"{pad}end")
            else:
                head = ftype
                if condition:
                    head = f"{ftype} [{condition}]"
                lines.append(f"{pad}{head} {label}")
                for nested in _as_dict_entries(
                    frag.get("messages", []), field="fragments.messages"
                ):
                    frm = _as_str(nested.get("from"), field="fragments.messages.from")
                    to = _as_str(nested.get("to"), field="fragments.messages.to")
                    ntext = _as_str(nested.get("text"), field="fragments.messages.text")
                    ntype = _as_str(nested.get("type", "sync"), field="fragments.messages.type")
                    lines.append(f"{pad}  {frm}{_arrow_for(ntype)}{to}: {ntext}")
                lines.append(f"{pad}end")

    _emit_fragment(fragments, indent=1)
    return "\n".join(lines) + "\n", "diagram.mmd"


render_mermaid_sequence = _render_mermaid_sequence
