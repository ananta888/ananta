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

_VALID_PARTICIPANT_KINDS = {"participant", "actor", "boundary", "control", "entity", "database"}
_VALID_FRAGMENT_TYPES = {"alt", "par", "loop", "opt", "critical"}


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

    participant_kinds = _validate_participants(participants)
    seen_ids = set(participant_kinds)
    _validate_messages(messages, seen_ids)
    _validate_fragment_tree(fragments, path="fragments", seen_ids=seen_ids)

    lines: list[str] = ["sequenceDiagram"]
    if diagram_title:
        lines.append(f"  %% {diagram_title}")
    if autonumber:
        lines.append("  autonumber")
    _emit_participants(lines, participants, participant_kinds)
    _emit_messages(lines, messages)
    _emit_fragments(lines, fragments, indent=1)
    return "\n".join(lines) + "\n", "diagram.mmd"


def _validate_participants(participants: list[dict]) -> dict[str, str]:
    """Validate participant ids/kinds; return kind by id in declaration order."""
    participant_kinds: dict[str, str] = {}
    for p in participants:
        pid = _as_str(p.get("id"), field="participants[].id")
        _check_identifier(pid, field="participants[].id")
        if pid in participant_kinds:
            raise NotationRenderError(f"duplicate participant id {pid!r}")
        kind = _as_str(p.get("kind", "participant"), field="participants[].kind")
        if kind not in _VALID_PARTICIPANT_KINDS:
            raise NotationRenderError(
                f"participant {pid!r} has unknown kind {kind!r}; "
                f"expected one of {sorted(_VALID_PARTICIPANT_KINDS)}"
            )
        participant_kinds[pid] = kind
    return participant_kinds


def _validate_messages(messages: list[dict], seen_ids: set[str]) -> None:
    for m in messages:
        frm = _as_str(m.get("from"), field="messages[].from")
        to = _as_str(m.get("to"), field="messages[].to")
        if frm not in seen_ids:
            raise NotationRenderError(f"message from {frm!r} references unknown participant")
        if to not in seen_ids:
            raise NotationRenderError(f"message to {to!r} references unknown participant")
        if not _as_str(m.get("text"), field="messages[].text"):
            raise NotationRenderError("message text must be a non-empty string")


def _validate_fragment_tree(frag_list: list[dict], *, path: str, seen_ids: set[str]) -> None:
    for idx, frag in enumerate(frag_list):
        ftype = _as_str(frag.get("type"), field=f"{path}[{idx}].type")
        if ftype not in _VALID_FRAGMENT_TYPES:
            raise NotationRenderError(
                f"{path}[{idx}].type {ftype!r} must be one of {sorted(_VALID_FRAGMENT_TYPES)}"
            )
        label = frag.get("label")
        if not isinstance(label, str) or not label.strip():
            raise NotationRenderError(f"{path}[{idx}].label must be a non-empty string")
        if ftype == "alt":
            _validate_alt_branches(frag, path=f"{path}[{idx}]", seen_ids=seen_ids)
        else:
            nested_msgs = _as_list(frag.get("messages", []), field=f"{path}[{idx}].messages")
            _validate_nested_messages(
                nested_msgs,
                seen_ids=seen_ids,
                not_dict_error=f"{path}[{idx}].messages must be a list of dicts",
                origin="fragment message",
            )


def _validate_alt_branches(frag: dict, *, path: str, seen_ids: set[str]) -> None:
    branches = frag.get("branches")
    if not isinstance(branches, list) or not branches:
        raise NotationRenderError(f"{path}.branches must be a non-empty list")
    for bidx, branch in enumerate(branches):
        if not isinstance(branch, dict):
            raise NotationRenderError(f"{path}.branches[{bidx}] must be a dict")
        cond = branch.get("condition")
        if not isinstance(cond, str) or not cond.strip():
            raise NotationRenderError(f"{path}.branches[{bidx}].condition must be a non-empty string")
        nested_msgs = _as_list(
            branch.get("messages", []),
            field=f"{path}.branches[{bidx}].messages",
        )
        _validate_nested_messages(
            nested_msgs,
            seen_ids=seen_ids,
            not_dict_error=f"{path}.branches[{bidx}].messages must be a list of dicts",
            origin="alt branch message",
        )


def _validate_nested_messages(nested_msgs: list, *, seen_ids: set[str], not_dict_error: str, origin: str) -> None:
    for nested in nested_msgs:
        if not isinstance(nested, dict):
            raise NotationRenderError(not_dict_error)
        frm = nested.get("from")
        to = nested.get("to")
        if frm not in seen_ids:
            raise NotationRenderError(f"{origin} from {frm!r} references unknown participant")
        if to not in seen_ids:
            raise NotationRenderError(f"{origin} to {to!r} references unknown participant")


def _arrow_for(msg_type: str) -> str:
    if msg_type == "async":
        return "--)"
    if msg_type == "return":
        return "-->>"
    return "->>"


def _emit_participants(lines: list[str], participants: list[dict], participant_kinds: dict[str, str]) -> None:
    for p in participants:
        pid = _as_str(p.get("id"), field="participants[].id")
        label = _as_str(p.get("label", pid), field="participants[].label")
        keyword = "actor" if participant_kinds[pid] == "actor" else "participant"
        if label == pid:
            lines.append(f"  {keyword} {pid}")
        else:
            lines.append(f"  {keyword} {pid} as {label}")


def _emit_messages(lines: list[str], messages: list[dict]) -> None:
    for m in messages:
        frm = _as_str(m.get("from"), field="messages[].from")
        to = _as_str(m.get("to"), field="messages[].to")
        text = _as_str(m.get("text"), field="messages[].text")
        msg_type = _as_str(m.get("type", "sync"), field="messages[].type")
        lines.append(f"  {frm}{_arrow_for(msg_type)}{to}: {text}")
        if _as_bool(m.get("activate"), default=False):
            lines.append(f"  activate {to}")
            lines.append(f"  deactivate {to}")


def _emit_nested_messages(lines: list[str], nested_messages: list[dict], *, prefix: str, field: str) -> None:
    for nested in nested_messages:
        frm = _as_str(nested.get("from"), field=f"{field}.from")
        to = _as_str(nested.get("to"), field=f"{field}.to")
        ntext = _as_str(nested.get("text"), field=f"{field}.text")
        ntype = _as_str(nested.get("type", "sync"), field=f"{field}.type")
        lines.append(f"{prefix}{frm}{_arrow_for(ntype)}{to}: {ntext}")


def _emit_fragments(lines: list[str], frag_list: list[dict], indent: int = 2) -> None:
    pad = "  " * indent
    for frag in frag_list:
        ftype = _as_str(frag.get("type"), field="fragments[].type")
        label = _as_str(frag.get("label"), field="fragments[].label")
        if ftype == "alt":
            _emit_alt_fragment(lines, frag, pad=pad, label=label)
        else:
            _emit_simple_fragment(lines, frag, pad=pad, ftype=ftype, label=label)


def _emit_alt_fragment(lines: list[str], frag: dict, *, pad: str, label: str) -> None:
    branches_list = _as_list(frag.get("branches", []), field="fragments.branches")
    first_cond = _as_str(
        _as_dict_entries(branches_list, field="fragments.branches")[0].get("condition"),
        field="fragments.branches[0].condition",
    )
    lines.append(f"{pad}alt {label}")
    lines.append(f"{pad}  {first_cond}")
    _emit_nested_messages(
        lines,
        _as_dict_entries(
            frag.get("branches", [{}])[0].get("messages", []),
            field="fragments.branches[0].messages",
        ),
        prefix=f"{pad}    ",
        field="fragments.branches[].messages",
    )
    for branch in _as_dict_entries(frag.get("branches", []), field="fragments.branches")[1:]:
        cond = _as_str(branch.get("condition"), field="fragments.branches[].condition")
        lines.append(f"{pad}else {cond}")
        _emit_nested_messages(
            lines,
            _as_dict_entries(branch.get("messages", []), field="fragments.branches[].messages"),
            prefix=f"{pad}    ",
            field="fragments.branches[].messages",
        )
    lines.append(f"{pad}end")


def _emit_simple_fragment(lines: list[str], frag: dict, *, pad: str, ftype: str, label: str) -> None:
    condition = frag.get("condition")
    head = f"{ftype} [{condition}]" if condition else ftype
    lines.append(f"{pad}{head} {label}")
    _emit_nested_messages(
        lines,
        _as_dict_entries(frag.get("messages", []), field="fragments.messages"),
        prefix=f"{pad}  ",
        field="fragments.messages",
    )
    lines.append(f"{pad}end")


render_mermaid_sequence = _render_mermaid_sequence
