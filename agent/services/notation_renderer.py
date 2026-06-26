"""Deterministic diagram-notation renderer.

Renders Mermaid and BPMN 2.0 diagram source from structured payloads.
The renderer is the counterpart of :class:`PatternTemplateRenderer` for
the ``diagram_notation`` pattern category. It is intentionally a
separate class — code patterns and notation patterns have different
shapes (parameter payload vs. file templates, single string output vs.
multiple files, structural validation vs. template substitution) and
mixing them would violate SRP.

Design contract (NOT-001):

* Deterministic: identical inputs -> byte-identical output. Element
  order in the input is preserved exactly (no set / no hashmap
  iteration in the output path).
* Pure: given the same ``pattern_plan`` it produces the same string,
  no I/O unless ``target_root`` is supplied (then it writes one file
  matching the notation's natural extension).
* Safe: rejects unknown element types, unknown arrow types, unknown
  node shapes, malformed identifiers, dangling references. All
  validation errors raise :class:`NotationRenderError`.
* Auditable: emits a :class:`NotationArtifact` with a stable
  ``sha256`` of the source and a ``manifest_sha256`` that covers
  the full render metadata (pattern_id, language, source hash,
  output filename).

Supported patterns:

* Mermaid: ``mermaid.class``, ``mermaid.sequence``, ``mermaid.state``,
  ``mermaid.usecase``, ``mermaid.activity``
* BPMN 2.0: ``bpmn.process``, ``bpmn.pool_lane``, ``bpmn.collaboration``

BPMN output conforms to the OMG BPMN 2.0 namespace
``http://www.omg.org/spec/BPMN/20100524/MODEL``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Iterable


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PSEUDOSTATE = "[*]"


class NotationRenderError(ValueError):
    """Raised when a notation pattern cannot be rendered safely.

    The error message is safe to log — it does not include the
    rendered source.
    """


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NotationArtifact:
    """One rendered notation output.

    For notation patterns a single render produces one source file
    (Mermaid ``.mmd`` or BPMN ``.bpmn``).
    """

    pattern_id: str
    language: str
    source: str
    sha256: str
    bytes_written: int
    output_filename: str

    @property
    def manifest_sha256(self) -> str:
        """Stable hash of the full render metadata."""
        payload = (
            f"{self.pattern_id}\t{self.language}\t"
            f"{self.output_filename}\t{self.sha256}\t{self.bytes_written}"
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "language": self.language,
            "source": self.source,
            "sha256": self.sha256,
            "bytes_written": self.bytes_written,
            "output_filename": self.output_filename,
            "manifest_sha256": self.manifest_sha256,
        }


# ---------------------------------------------------------------------------
# Parameter coercion helpers
# ---------------------------------------------------------------------------


def _as_str(value: Any, *, field: str) -> str:
    if value is None:
        raise NotationRenderError(f"parameter {field!r} is required")
    if not isinstance(value, str):
        raise NotationRenderError(
            f"parameter {field!r} must be a string, got {type(value).__name__}"
        )
    return value


def _as_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "yes", "1"):
            return True
        if lowered in ("false", "no", "0"):
            return False
    return bool(value)


def _as_list(value: Any, *, field: str) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        # glob_list-style: comma-separated or single JSON-encoded value
        stripped = value.strip()
        if not stripped:
            return []
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    return parsed
            except json.JSONDecodeError:
                pass
        return [item.strip() for item in stripped.split(",") if item.strip()]
    raise NotationRenderError(
        f"parameter {field!r} must be a list or string, got {type(value).__name__}"
    )


def _as_dict_entries(values: Iterable, *, field: str) -> list[dict]:
    """Coerce a list of (json-encoded string | dict) into a list of dicts.

    Notation pattern parameters in the catalog use ``glob_list`` of
    JSON-encoded dicts (e.g. ``'{"name": "Foo"}'``). The renderer
    accepts both the JSON-string form (from the catalog / pattern_plan)
    and the native dict form (from direct Python API calls).
    """
    result: list[dict] = []
    for idx, item in enumerate(values):
        if isinstance(item, dict):
            result.append(item)
            continue
        if isinstance(item, str):
            stripped = item.strip()
            if not stripped:
                continue
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise NotationRenderError(
                    f"{field}[{idx}] is not valid JSON: {exc}"
                ) from exc
            if not isinstance(parsed, dict):
                raise NotationRenderError(
                    f"{field}[{idx}] must decode to a JSON object, got "
                    f"{type(parsed).__name__}"
                )
            result.append(parsed)
            continue
        raise NotationRenderError(
            f"{field}[{idx}] must be a dict or JSON string, got "
            f"{type(item).__name__}"
        )
    return result


def _check_identifier(value: str, *, field: str) -> str:
    if not _IDENT_RE.match(value):
        raise NotationRenderError(
            f"{field!r} {value!r} must match ^[A-Za-z_][A-Za-z0-9_]*$"
        )
    return value


# ---------------------------------------------------------------------------
# Mermaid arrow map (canonical UML2 -> Mermaid glyphs)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Mermaid generators
# ---------------------------------------------------------------------------


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
                # Interface methods are abstract; render with `{abstract}`.
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
        # default and "sync"
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

    # Composite state references must point to known state ids.
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


# ---------------------------------------------------------------------------
# BPMN generators
# ---------------------------------------------------------------------------


_BPMN_NS = {
    "bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL",
    "bpmndi": "http://www.omg.org/spec/BPMN/20100524/DI",
    "dc": "http://www.omg.org/spec/DD/20100524/DC",
    "di": "http://www.omg.org/spec/DD/20100524/DI",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}

_BPMN_TARGET_NS = "http://bpmn.io/schema/bpmn"

_VALID_FLOW_ELEMENT_TYPES = {
    "startEvent", "endEvent", "intermediateThrowEvent", "intermediateCatchEvent",
    "userTask", "serviceTask", "scriptTask", "manualTask", "task",
    "exclusiveGateway", "inclusiveGateway", "parallelGateway", "eventBasedGateway",
}


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _render_bpmn_element(elem: dict) -> str:
    etype = _as_str(elem.get("type"), field="elements[].type")
    if etype not in _VALID_FLOW_ELEMENT_TYPES:
        raise NotationRenderError(
            f"element type {etype!r} must be one of "
            f"{sorted(_VALID_FLOW_ELEMENT_TYPES)}"
        )
    eid = _as_str(elem.get("id"), field="elements[].id")
    _check_identifier(eid, field="elements[].id")
    name = elem.get("name")
    attrs = f' id="{_xml_escape(eid)}"'
    if isinstance(name, str) and name:
        attrs += f' name="{_xml_escape(name)}"'
    return f"    <bpmn:{etype}{attrs} />"


def _render_bpmn_flow(flow: dict) -> str:
    fid = _as_str(flow.get("id"), field="flows[].id")
    _check_identifier(fid, field="flows[].id")
    src = _as_str(flow.get("sourceRef"), field="flows[].sourceRef")
    _check_identifier(src, field="flows[].sourceRef")
    tgt = _as_str(flow.get("targetRef"), field="flows[].targetRef")
    _check_identifier(tgt, field="flows[].targetRef")
    name = flow.get("name")
    cond = flow.get("conditionExpression")
    body = ""
    if isinstance(cond, str) and cond.strip():
        body = (
            f'<bpmn:conditionExpression xsi:type="bpmn:tFormalExpression">'
            f"{_xml_escape(cond.strip())}</bpmn:conditionExpression>"
        )
    attrs = (
        f' id="{_xml_escape(fid)}"'
        f' sourceRef="{_xml_escape(src)}"'
        f' targetRef="{_xml_escape(tgt)}"'
    )
    if isinstance(name, str) and name:
        attrs += f' name="{_xml_escape(name)}"'
    return f"    <bpmn:sequenceFlow{attrs}>{body}</bpmn:sequenceFlow>"


def _render_diagram_shape(elem_id: str, *, x: int, y: int) -> str:
    return (
        f'      <bpmndi:BPMNShape id="{_xml_escape(elem_id)}_di" '
        f'bpmnElement="{_xml_escape(elem_id)}">\n'
        f'        <dc:Bounds x="{x}" y="{y}" width="100" height="80" />\n'
        f'      </bpmndi:BPMNShape>'
    )


def _render_diagram_edge(flow_id: str) -> str:
    return (
        f'      <bpmndi:BPMNEdge id="{_xml_escape(flow_id)}_di" '
        f'bpmnElement="{_xml_escape(flow_id)}">\n'
        f'        <di:waypoint x="150" y="120" />\n'
        f'        <di:waypoint x="280" y="120" />\n'
        f'      </bpmndi:BPMNEdge>'
    )


def _definitions_open(definitions_id: str) -> str:
    ns_attrs = " ".join(f'xmlns:{k}="{v}"' for k, v in _BPMN_NS.items())
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<bpmn:definitions id="{_xml_escape(definitions_id)}" {ns_attrs} '
        f'targetNamespace="{_BPMN_TARGET_NS}">'
    )


def _validate_flow_elements(elements: list[dict]) -> list[str]:
    seen: set[str] = set()
    start_count = 0
    element_ids: list[str] = []
    for elem in elements:
        etype = _as_str(elem.get("type"), field="elements[].type")
        eid = _as_str(elem.get("id"), field="elements[].id")
        _check_identifier(eid, field="elements[].id")
        if eid in seen:
            raise NotationRenderError(f"duplicate element id {eid!r}")
        seen.add(eid)
        element_ids.append(eid)
        if etype not in _VALID_FLOW_ELEMENT_TYPES:
            raise NotationRenderError(
                f"element type {etype!r} must be one of "
                f"{sorted(_VALID_FLOW_ELEMENT_TYPES)}"
            )
        if etype == "startEvent":
            start_count += 1
    if start_count != 1:
        raise NotationRenderError(
            f"process must contain exactly one startEvent, found {start_count}"
        )
    return element_ids


def _validate_flows(flows: list[dict], known_ids: set[str]) -> None:
    seen_flow_ids: set[str] = set()
    for flow in flows:
        fid = _as_str(flow.get("id"), field="flows[].id")
        _check_identifier(fid, field="flows[].id")
        if fid in seen_flow_ids:
            raise NotationRenderError(f"duplicate flow id {fid!r}")
        seen_flow_ids.add(fid)
        src = _as_str(flow.get("sourceRef"), field="flows[].sourceRef")
        tgt = _as_str(flow.get("targetRef"), field="flows[].targetRef")
        if src not in known_ids:
            raise NotationRenderError(
                f"flow {fid!r} sourceRef {src!r} references unknown element"
            )
        if tgt not in known_ids:
            raise NotationRenderError(
                f"flow {fid!r} targetRef {tgt!r} references unknown element"
            )


def _render_bpmn_process(params: dict[str, Any]) -> tuple[str, str]:
    definitions_id = _as_str(params.get("definitions_id"), field="definitions_id")
    _check_identifier(definitions_id, field="definitions_id")
    process_id = _as_str(params.get("process_id"), field="process_id")
    _check_identifier(process_id, field="process_id")
    process_name = params.get("process_name") or ""
    elements_raw = _as_list(params.get("elements"), field="elements")
    if not elements_raw:
        raise NotationRenderError("elements must be a non-empty list")
    elements = _as_dict_entries(elements_raw, field="elements")
    flows_raw = _as_list(params.get("flows"), field="flows")
    flows = _as_dict_entries(flows_raw, field="flows")

    element_ids = _validate_flow_elements(elements)
    _validate_flows(flows, set(element_ids))

    lines: list[str] = [_definitions_open(definitions_id)]
    proc_open = f'  <bpmn:process id="{_xml_escape(process_id)}" isExecutable="true"'
    if isinstance(process_name, str) and process_name:
        proc_open += f' name="{_xml_escape(process_name)}"'
    proc_open += ">"
    lines.append(proc_open)
    for elem in elements:
        lines.append(_render_bpmn_element(elem))
    for flow in flows:
        lines.append(_render_bpmn_flow(flow))
    lines.append("  </bpmn:process>")

    lines.append("  <bpmndi:BPMNDiagram id=" + '"BPMNDiagram_1">')
    lines.append('    <bpmndi:BPMNPlane id="BPMNPlane_1" bpmnElement="' +
                 _xml_escape(process_id) + '">')
    x = 160
    y = 120
    for idx, eid in enumerate(element_ids):
        lines.append(_render_diagram_shape(eid, x=x + idx * 140, y=y))
    for flow in flows:
        fid = _as_str(flow.get("id"), field="flows[].id")
        lines.append(_render_diagram_edge(fid))
    lines.append("    </bpmndi:BPMNPlane>")
    lines.append("  </bpmndi:BPMNDiagram>")
    lines.append("</bpmn:definitions>")
    return "\n".join(lines) + "\n", "process.bpmn"


def _render_bpmn_pool_lane(params: dict[str, Any]) -> tuple[str, str]:
    definitions_id = _as_str(params.get("definitions_id"), field="definitions_id")
    _check_identifier(definitions_id, field="definitions_id")
    process_id = _as_str(params.get("process_id"), field="process_id")
    _check_identifier(process_id, field="process_id")
    process_name = params.get("process_name") or ""
    elements_raw = _as_list(params.get("elements"), field="elements")
    if not elements_raw:
        raise NotationRenderError("elements must be a non-empty list")
    elements = _as_dict_entries(elements_raw, field="elements")
    flows_raw = _as_list(params.get("flows"), field="flows")
    flows = _as_dict_entries(flows_raw, field="flows")
    lanes_raw = _as_list(params.get("lanes"), field="lanes")
    if not lanes_raw:
        raise NotationRenderError("lanes must be a non-empty list")
    lanes = _as_dict_entries(lanes_raw, field="lanes")

    element_ids = _validate_flow_elements(elements)
    _validate_flows(flows, set(element_ids))

    seen_lane_ids: set[str] = set()
    lane_refs: list[set[str]] = []
    for lane in lanes:
        lid = _as_str(lane.get("id"), field="lanes[].id")
        _check_identifier(lid, field="lanes[].id")
        if lid in seen_lane_ids:
            raise NotationRenderError(f"duplicate lane id {lid!r}")
        seen_lane_ids.add(lid)
        refs = _as_list(lane.get("flow_node_refs", []), field="lanes[].flow_node_refs")
        ref_set: set[str] = set()
        for ref in refs:
            if not isinstance(ref, str):
                raise NotationRenderError(
                    f"lane {lid!r} flow_node_refs entries must be strings"
                )
            ref_set.add(ref)
        lane_refs.append(ref_set)

    element_set = set(element_ids)
    union: set[str] = set()
    for ref_set in lane_refs:
        for ref in ref_set:
            if ref not in element_set:
                raise NotationRenderError(
                    f"lane flow_node_ref {ref!r} references unknown element"
                )
            if ref in union:
                raise NotationRenderError(
                    f"element {ref!r} assigned to multiple lanes"
                )
            union.add(ref)
    if union != element_set:
        missing = element_set - union
        raise NotationRenderError(
            f"elements not assigned to any lane: {sorted(missing)}"
        )

    lines: list[str] = [_definitions_open(definitions_id)]
    proc_open = f'  <bpmn:process id="{_xml_escape(process_id)}" isExecutable="true"'
    if isinstance(process_name, str) and process_name:
        proc_open += f' name="{_xml_escape(process_name)}"'
    proc_open += ">"
    lines.append(proc_open)
    lines.append("    <bpmn:laneSet id=\"LaneSet_1\">")
    for lane in lanes:
        lid = _as_str(lane.get("id"), field="lanes[].id")
        lname = _as_str(lane.get("name", lid), field="lanes[].name")
        lines.append(f'      <bpmn:lane id="{_xml_escape(lid)}" '
                     f'name="{_xml_escape(lname)}">')
        for ref in _as_list(lane.get("flow_node_refs", []),
                            field="lanes[].flow_node_refs"):
            lines.append(f'        <bpmn:flowNodeRef>{_xml_escape(ref)}'
                         f'</bpmn:flowNodeRef>')
        lines.append("      </bpmn:lane>")
    lines.append("    </bpmn:laneSet>")
    for elem in elements:
        lines.append(_render_bpmn_element(elem))
    for flow in flows:
        lines.append(_render_bpmn_flow(flow))
    lines.append("  </bpmn:process>")

    lines.append('  <bpmndi:BPMNDiagram id="BPMNDiagram_1">')
    lines.append('    <bpmndi:BPMNPlane id="BPMNPlane_1" bpmnElement="' +
                 _xml_escape(process_id) + '">')
    y_base = 120
    for li, lane in enumerate(lanes):
        lid = _as_str(lane.get("id"), field="lanes[].id")
        lname = _as_str(lane.get("name", lid), field="lanes[].name")
        # Lane shape: tall rectangle on the left
        lines.append(
            f'      <bpmndi:BPMNShape id="{_xml_escape(lid)}_di" '
            f'bpmnElement="{_xml_escape(lid)}" isHorizontal="true">\n'
            f'        <dc:Bounds x="120" y="{y_base + li * 140}" '
            f'width="900" height="120" />\n'
            f'      </bpmndi:BPMNShape>'
        )
        # Element shapes inside the lane
        for idx, ref in enumerate(_as_list(lane.get("flow_node_refs", []),
                                           field="lanes[].flow_node_refs")):
            lines.append(_render_diagram_shape(
                ref, x=160 + idx * 140, y=y_base + li * 140 + 20
            ))
    for flow in flows:
        fid = _as_str(flow.get("id"), field="flows[].id")
        lines.append(_render_diagram_edge(fid))
    lines.append("    </bpmndi:BPMNPlane>")
    lines.append("  </bpmndi:BPMNDiagram>")
    lines.append("</bpmn:definitions>")
    return "\n".join(lines) + "\n", "process.bpmn"


def _render_bpmn_collaboration(params: dict[str, Any]) -> tuple[str, str]:
    definitions_id = _as_str(params.get("definitions_id"), field="definitions_id")
    _check_identifier(definitions_id, field="definitions_id")
    participants_raw = _as_list(params.get("participants"), field="participants")
    if not participants_raw:
        raise NotationRenderError("participants must be a non-empty list")
    participants = _as_dict_entries(participants_raw, field="participants")
    message_flows_raw = _as_list(params.get("message_flows"), field="message_flows")
    message_flows = _as_dict_entries(message_flows_raw, field="message_flows")

    seen_part_ids: set[str] = set()
    seen_proc_ids: set[str] = set()
    participant_data: list[dict] = []
    for p in participants:
        pid = _as_str(p.get("id"), field="participants[].id")
        _check_identifier(pid, field="participants[].id")
        if pid in seen_part_ids:
            raise NotationRenderError(f"duplicate participant id {pid!r}")
        seen_part_ids.add(pid)
        proc_id = _as_str(p.get("process_id"), field="participants[].process_id")
        _check_identifier(proc_id, field="participants[].process_id")
        if proc_id in seen_proc_ids:
            raise NotationRenderError(
                f"duplicate process_id {proc_id!r} across participants"
            )
        seen_proc_ids.add(proc_id)
        participant_data.append(p)

    seen_mf_ids: set[str] = set()
    for mf in message_flows:
        mid = _as_str(mf.get("id"), field="message_flows[].id")
        _check_identifier(mid, field="message_flows[].id")
        if mid in seen_mf_ids:
            raise NotationRenderError(f"duplicate messageFlow id {mid!r}")
        seen_mf_ids.add(mid)
        src = _as_str(mf.get("source_ref"), field="message_flows[].source_ref")
        tgt = _as_str(mf.get("target_ref"), field="message_flows[].target_ref")
        if src not in seen_part_ids:
            raise NotationRenderError(
                f"messageFlow {mid!r} source_ref {src!r} references "
                f"unknown participant"
            )
        if tgt not in seen_part_ids:
            raise NotationRenderError(
                f"messageFlow {mid!r} target_ref {tgt!r} references "
                f"unknown participant"
            )

    lines: list[str] = [_definitions_open(definitions_id)]

    for p in participant_data:
        pid = _as_str(p.get("id"), field="participants[].id")
        pname = _as_str(p.get("name", pid), field="participants[].name")
        proc_id = _as_str(p.get("process_id"), field="participants[].process_id")
        proc_name = p.get("process_name") or ""
        elements_raw = _as_list(p.get("elements", []),
                                field="participants[].elements")
        if not elements_raw:
            raise NotationRenderError(
                f"participant {pid!r} must carry at least one element"
            )
        elements = _as_dict_entries(elements_raw,
                                    field="participants[].elements")
        flows_raw = _as_list(p.get("flows", []),
                             field="participants[].flows")
        flows = _as_dict_entries(flows_raw, field="participants[].flows")
        element_ids = _validate_flow_elements(elements)
        _validate_flows(flows, set(element_ids))

        proc_open = (
            f'  <bpmn:process id="{_xml_escape(proc_id)}" isExecutable="true"'
        )
        if isinstance(proc_name, str) and proc_name:
            proc_open += f' name="{_xml_escape(proc_name)}"'
        proc_open += ">"
        lines.append(proc_open)

        lanes_raw = _as_list(p.get("lanes", []),
                             field="participants[].lanes")
        if lanes_raw:
            lanes = _as_dict_entries(lanes_raw,
                                     field="participants[].lanes")
            seen_lane_ids: set[str] = set()
            lane_refs: list[set[str]] = []
            element_set = set(element_ids)
            for lane in lanes:
                lid = _as_str(lane.get("id"), field="participants[].lanes[].id")
                _check_identifier(lid,
                                  field="participants[].lanes[].id")
                if lid in seen_lane_ids:
                    raise NotationRenderError(
                        f"duplicate lane id {lid!r} in participant {pid!r}"
                    )
                seen_lane_ids.add(lid)
                refs = _as_list(lane.get("flow_node_refs", []),
                                field="participants[].lanes[].flow_node_refs")
                ref_set: set[str] = set()
                for ref in refs:
                    if not isinstance(ref, str):
                        raise NotationRenderError(
                            f"lane {lid!r} flow_node_refs entries must be strings"
                        )
                    ref_set.add(ref)
                lane_refs.append(ref_set)
            union: set[str] = set()
            for ref_set in lane_refs:
                for ref in ref_set:
                    if ref not in element_set:
                        raise NotationRenderError(
                            f"lane flow_node_ref {ref!r} references unknown "
                            f"element in participant {pid!r}"
                        )
                    if ref in union:
                        raise NotationRenderError(
                            f"element {ref!r} assigned to multiple lanes in "
                            f"participant {pid!r}"
                        )
                    union.add(ref)
            if union != element_set:
                missing = element_set - union
                raise NotationRenderError(
                    f"elements not assigned to any lane in participant "
                    f"{pid!r}: {sorted(missing)}"
                )
            lines.append('    <bpmn:laneSet id="LaneSet_' + _xml_escape(proc_id) + '">')
            for lane in lanes:
                lid = _as_str(lane.get("id"),
                              field="participants[].lanes[].id")
                lname = _as_str(lane.get("name", lid),
                                field="participants[].lanes[].name")
                lines.append(
                    f'      <bpmn:lane id="{_xml_escape(lid)}" '
                    f'name="{_xml_escape(lname)}">'
                )
                for ref in _as_list(lane.get("flow_node_refs", []),
                                    field="participants[].lanes[].flow_node_refs"):
                    lines.append(
                        f'        <bpmn:flowNodeRef>{_xml_escape(ref)}'
                        f'</bpmn:flowNodeRef>'
                    )
                lines.append("      </bpmn:lane>")
            lines.append("    </bpmn:laneSet>")

        for elem in elements:
            lines.append(_render_bpmn_element(elem))
        for flow in flows:
            lines.append(_render_bpmn_flow(flow))
        lines.append("  </bpmn:process>")

    lines.append('  <bpmn:collaboration id="Collaboration_1">')
    for p in participant_data:
        pid = _as_str(p.get("id"), field="participants[].id")
        pname = _as_str(p.get("name", pid), field="participants[].name")
        proc_id = _as_str(p.get("process_id"),
                          field="participants[].process_id")
        lines.append(
            f'    <bpmn:participant id="{_xml_escape(pid)}" '
            f'name="{_xml_escape(pname)}" processRef="{_xml_escape(proc_id)}" />'
        )
    for mf in message_flows:
        mid = _as_str(mf.get("id"), field="message_flows[].id")
        src = _as_str(mf.get("source_ref"), field="message_flows[].source_ref")
        tgt = _as_str(mf.get("target_ref"), field="message_flows[].target_ref")
        name = mf.get("name")
        attrs = (
            f' id="{_xml_escape(mid)}" '
            f'sourceRef="{_xml_escape(src)}" '
            f'targetRef="{_xml_escape(tgt)}"'
        )
        if isinstance(name, str) and name:
            attrs += f' name="{_xml_escape(name)}"'
        lines.append(f"    <bpmn:messageFlow{attrs} />")
    lines.append("  </bpmn:collaboration>")

    lines.append('  <bpmndi:BPMNDiagram id="BPMNDiagram_1">')
    lines.append('    <bpmndi:BPMNPlane id="BPMNPlane_1" bpmnElement="Collaboration_1">')
    y_pool = 120
    for pi, p in enumerate(participant_data):
        pid = _as_str(p.get("id"), field="participants[].id")
        pname = _as_str(p.get("name", pid), field="participants[].name")
        proc_id = _as_str(p.get("process_id"),
                          field="participants[].process_id")
        # Pool container
        lines.append(
            f'      <bpmndi:BPMNShape id="{_xml_escape(pid)}_di" '
            f'bpmnElement="{_xml_escape(pid)}" isHorizontal="true">\n'
            f'        <dc:Bounds x="120" y="{y_pool + pi * 220}" '
            f'width="900" height="200" />\n'
            f'      </bpmndi:BPMNShape>'
        )
        # Process plane (child of pool)
        lines.append(
            f'      <bpmndi:BPMNShape id="{_xml_escape(proc_id)}_plane" '
            f'bpmnElement="{_xml_escape(proc_id)}" isHorizontal="true">\n'
            f'        <dc:Bounds x="160" y="{y_pool + pi * 220 + 20}" '
            f'width="840" height="160" />\n'
            f'      </bpmndi:BPMNShape>'
        )
        # Element shapes
        elements_raw = _as_list(p.get("elements", []),
                                field="participants[].elements")
        for idx, raw_elem in enumerate(elements_raw):
            elem = _as_dict_entries([raw_elem],
                                    field="participants[].elements")[0]
            eid = _as_str(elem.get("id"), field="participants[].elements[].id")
            lines.append(_render_diagram_shape(
                eid,
                x=200 + idx * 140,
                y=y_pool + pi * 220 + 60,
            ))
        # Sequence flows as edges
        flows_raw = _as_list(p.get("flows", []),
                             field="participants[].flows")
        for raw_flow in flows_raw:
            flow = _as_dict_entries([raw_flow],
                                    field="participants[].flows")[0]
            fid = _as_str(flow.get("id"), field="participants[].flows[].id")
            lines.append(_render_diagram_edge(fid))
    for mf in message_flows:
        mid = _as_str(mf.get("id"), field="message_flows[].id")
        lines.append(
            f'      <bpmndi:BPMNEdge id="{_xml_escape(mid)}_di" '
            f'bpmnElement="{_xml_escape(mid)}">\n'
            f'        <di:waypoint x="1020" y="220" />\n'
            f'        <di:waypoint x="1020" y="440" />\n'
            f'      </bpmndi:BPMNEdge>'
        )
    lines.append("    </bpmndi:BPMNPlane>")
    lines.append("  </bpmndi:BPMNDiagram>")
    lines.append("</bpmn:definitions>")
    return "\n".join(lines) + "\n", "collaboration.bpmn"


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------


_PATTERN_TO_GENERATOR = {
    "mermaid.class": _render_mermaid_class,
    "mermaid.sequence": _render_mermaid_sequence,
    "mermaid.state": _render_mermaid_state,
    "mermaid.usecase": _render_mermaid_usecase,
    "mermaid.activity": _render_mermaid_activity,
    "bpmn.process": _render_bpmn_process,
    "bpmn.pool_lane": _render_bpmn_pool_lane,
    "bpmn.collaboration": _render_bpmn_collaboration,
}


_PATTERN_TO_FILENAME = {
    "mermaid.class": "diagram.mmd",
    "mermaid.sequence": "diagram.mmd",
    "mermaid.state": "diagram.mmd",
    "mermaid.usecase": "diagram.mmd",
    "mermaid.activity": "diagram.mmd",
    "bpmn.process": "process.bpmn",
    "bpmn.pool_lane": "process.bpmn",
    "bpmn.collaboration": "collaboration.bpmn",
}


class NotationRenderer:
    """Render diagram notation patterns (Mermaid / BPMN 2.0).

    Stateless and safe to share across threads.
    """

    def render(
        self,
        *,
        pattern_plan: dict[str, Any],
        target_root: str | None = None,
    ) -> NotationArtifact:
        """Render a single notation pattern plan.

        Args:
            pattern_plan: validated pattern dict with ``pattern_id``,
                ``language`` and ``parameters`` (flat dict).
            target_root: directory to write the rendered file to.
                When ``None`` the renderer runs in dry-run mode.

        Returns:
            A :class:`NotationArtifact` with stable sha256 hashes.

        Raises:
            NotationRenderError: on validation errors, unknown
                pattern_id, or unsafe output paths.
        """
        pattern_id = _as_str(pattern_plan.get("pattern_id"),
                             field="pattern_plan.pattern_id")
        generator = _PATTERN_TO_GENERATOR.get(pattern_id)
        if generator is None:
            raise NotationRenderError(
                f"unknown notation pattern_id {pattern_id!r}; expected one of "
                f"{sorted(_PATTERN_TO_GENERATOR)}"
            )

        language = _as_str(pattern_plan.get("language"),
                           field="pattern_plan.language")
        if language not in {"mermaid", "bpmn"}:
            raise NotationRenderError(
                f"notation pattern language must be 'mermaid' or 'bpmn', "
                f"got {language!r}"
            )

        params = self._resolve_params(pattern_plan)
        source, default_filename = generator(params)

        output_filename = _PATTERN_TO_FILENAME.get(pattern_id, default_filename)
        sha = hashlib.sha256(source.encode("utf-8")).hexdigest()
        artifact = NotationArtifact(
            pattern_id=pattern_id,
            language=language,
            source=source,
            sha256=sha,
            bytes_written=len(source.encode("utf-8")),
            output_filename=output_filename,
        )

        if target_root:
            self._write_to_disk(artifact, target_root)
        return artifact

    # --- internals ------------------------------------------------------

    @staticmethod
    def _resolve_params(pattern_plan: dict[str, Any]) -> dict[str, Any]:
        """Resolve parameters using the same precedence as the
        template renderer: ``parameters_provided`` > flat ``parameters``.
        """
        flat = pattern_plan.get("parameters_provided")
        if flat is None:
            parameters = pattern_plan.get("parameters")
            if isinstance(parameters, dict):
                flat = parameters
            elif isinstance(parameters, list):
                flat = {}
            else:
                raise NotationRenderError(
                    "pattern_plan parameters must be a dict (flat) or "
                    "a list (schema array)"
                )
        if not isinstance(flat, dict):
            raise NotationRenderError("pattern_plan parameters must be a dict")
        # Implicit parameters (pattern_id, language) — match template renderer.
        implicit = {}
        for implicit_key in ("pattern_id", "language"):
            val = pattern_plan.get(implicit_key)
            if val is not None:
                implicit[implicit_key] = val
        return {**flat, **implicit}

    @staticmethod
    def _write_to_disk(artifact: NotationArtifact, target_root: str) -> None:
        if os.path.isabs(artifact.output_filename):
            raise NotationRenderError(
                f"output_filename {artifact.output_filename!r} must be relative"
            )
        normalised = os.path.normpath(artifact.output_filename)
        if normalised.startswith("..") or "/.." in f"/{normalised}" or normalised == "..":
            raise NotationRenderError(
                f"output_filename {artifact.output_filename!r} escapes "
                f"the target root"
            )
        full = os.path.abspath(os.path.join(target_root, normalised))
        root_abs = os.path.abspath(target_root) + os.sep
        if not (full + os.sep).startswith(root_abs):
            raise NotationRenderError(
                f"output_filename {artifact.output_filename!r} resolves "
                f"outside target_root"
            )
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(artifact.source)
        with open(full, "rb") as f:
            actual_sha = hashlib.sha256(f.read()).hexdigest()
        if actual_sha != artifact.sha256:
            raise NotationRenderError(
                f"hash mismatch for {artifact.output_filename!r} after write"
            )


_default_renderer: NotationRenderer | None = None


def get_notation_renderer() -> NotationRenderer:
    """Return the shared renderer (stateless, safe to share)."""
    global _default_renderer
    if _default_renderer is None:
        _default_renderer = NotationRenderer()
    return _default_renderer


def reset_notation_renderer_singleton() -> None:
    """Test helper to drop the cached singleton."""
    global _default_renderer
    _default_renderer = None