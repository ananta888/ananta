from __future__ import annotations

import re
from typing import Any

from rag_helper.extractors.java_type_resolution import uniq_keep_order
from rag_helper.utils.ids import safe_id
from rag_helper.utils.text_normalization import normalize_ws


def node_text(src: bytes, node) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")


def first_child_of_type(node, typ: str):
    for c in node.children:
        if c.type == typ:
            return c
    return None


def extract_package(root, src: bytes) -> str | None:
    for child in root.children:
        if child.type == "package_declaration":
            return node_text(src, child).replace("package", "").replace(";", "").strip()
    return None


def extract_imports(root, src: bytes) -> list[str]:
    result = []
    for child in root.children:
        if child.type == "import_declaration":
            txt = node_text(src, child)
            txt = txt.replace("import", "").replace(";", "").strip()
            txt = txt.replace("static ", "static:")
            result.append(txt)
    return result


def extract_modifiers(node, src: bytes) -> list[str]:
    mod_node = first_child_of_type(node, "modifiers")
    if not mod_node:
        return []
    return [x.strip() for x in node_text(src, mod_node).split() if x.strip() and not x.strip().startswith("@")]


def extract_annotations(node, src: bytes) -> list[str]:
    mod_node = first_child_of_type(node, "modifiers")
    anns = []
    if mod_node:
        for c in mod_node.children:
            if c.type == "annotation":
                anns.append(node_text(src, c).strip())
    return anns


def extract_javadoc(node, src: bytes) -> str | None:
    start_byte = getattr(node, "start_byte", None)
    if not isinstance(start_byte, int) or start_byte <= 0:
        return None

    end = start_byte
    while end > 0 and chr(src[end - 1]).isspace():
        end -= 1
    if end < 2 or src[end - 2:end] != b"*/":
        return None

    start = src.rfind(b"/**", 0, end - 2)
    if start < 0:
        return None

    raw = src[start:end].decode("utf-8", errors="ignore")
    cleaned = _clean_javadoc(raw)
    return cleaned or None


def extract_javadoc_summary(javadoc: str | None) -> str | None:
    if not javadoc:
        return None

    description_lines: list[str] = []
    for line in javadoc.split("\n"):
        stripped = line.strip()
        if stripped.startswith("@"):
            break
        if stripped:
            description_lines.append(stripped)

    summary_source = " ".join(description_lines).strip()
    if not summary_source:
        summary_source = next((line.strip() for line in javadoc.split("\n") if line.strip()), "")
    if not summary_source:
        return None

    sentence_match = re.search(r"(.+?[.!?])(?:\s|$)", summary_source)
    if sentence_match:
        return sentence_match.group(1).strip()
    return summary_source[:220].rstrip()


def _clean_javadoc(raw: str) -> str:
    body = raw[3:-2].replace("\r\n", "\n").replace("\r", "\n")
    cleaned_lines: list[str] = []
    previous_blank = False
    for line in body.split("\n"):
        stripped = line.strip()
        if stripped.startswith("*"):
            stripped = stripped[1:].lstrip()
        stripped = re.sub(r"\s+", " ", stripped).strip()
        if not stripped:
            if cleaned_lines and not previous_blank:
                cleaned_lines.append("")
            previous_blank = True
            continue
        cleaned_lines.append(stripped)
        previous_blank = False
    while cleaned_lines and cleaned_lines[-1] == "":
        cleaned_lines.pop()
    return "\n".join(cleaned_lines).strip()


def extract_identifier(node, src: bytes) -> str | None:
    ident = first_child_of_type(node, "identifier")
    if ident:
        return node_text(src, ident).strip()
    return None


def extract_return_type(node, src: bytes) -> str | None:
    for c in node.children:
        if c.type in {
            "type_identifier", "generic_type", "integral_type", "floating_point_type",
            "boolean_type", "void_type", "array_type", "scoped_type_identifier",
            "annotated_type",
        }:
            return node_text(src, c).strip()
    return None


def extract_field(node, src: bytes) -> dict[str, Any]:
    mods = extract_modifiers(node, src)
    anns = extract_annotations(node, src)
    javadoc = extract_javadoc(node, src)
    typ = None
    declarators = []

    for c in node.children:
        if c.type in {
            "type_identifier", "generic_type", "integral_type", "floating_point_type",
            "boolean_type", "array_type", "scoped_type_identifier", "annotated_type",
        } and typ is None:
            typ = node_text(src, c).strip()
        elif c.type == "variable_declarator":
            declarators.append(normalize_ws(node_text(src, c)))

    return {
        "type": typ,
        "declarators": declarators,
        "modifiers": mods,
        "annotations": anns,
        "javadoc": javadoc,
        "javadoc_summary": extract_javadoc_summary(javadoc),
    }


def extract_parameters(node, src: bytes) -> list[str]:
    params = []
    fp = first_child_of_type(node, "formal_parameters")
    if fp:
        for c in fp.children:
            if c.type == "formal_parameter":
                params.append(normalize_ws(node_text(src, c)))
    return params


def extract_method_calls(node, src: bytes) -> list[str]:
    if node is None:
        return []
    calls = []
    stack = [node]
    while stack:
        cur = stack.pop()
        if cur.type == "method_invocation":
            calls.append(normalize_ws(node_text(src, cur)))
        stack.extend(reversed(cur.children))
    return uniq_keep_order([c[:250] for c in calls], limit=200)


def extract_type_refs(node, src: bytes) -> list[str]:
    refs = []
    stack = [node]
    while stack:
        cur = stack.pop()
        if cur.type in {
            "type_identifier", "generic_type", "scoped_type_identifier",
            "array_type", "annotated_type",
        }:
            refs.append(normalize_ws(node_text(src, cur)))
        stack.extend(reversed(cur.children))
    return uniq_keep_order(refs, limit=200)


def extract_extends_implements(node, src: bytes) -> tuple[str | None, list[str]]:
    extends = None
    implements = []

    for c in node.children:
        if c.type == "superclass":
            extends = normalize_ws(node_text(src, c).replace("extends", ""))
        elif c.type == "super_interfaces":
            raw = normalize_ws(node_text(src, c).replace("implements", ""))
            implements = [x.strip() for x in raw.split(",") if x.strip()]

    return extends, implements


def make_relation(
    file: str,
    source_id: str,
    source_kind: str,
    source_name: str,
    relation: str,
    target: str,
    target_resolved: str | None = None,
    weight: int = 1,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "kind": "relation",
        "file": file,
        "id": f"relation:{safe_id(file, source_id, relation, target, target_resolved or '')}",
        "source_id": source_id,
        "source_kind": source_kind,
        "source_name": source_name,
        "relation": relation,
        "target": target,
        "target_resolved": target_resolved,
        "weight": weight,
    }
    if extra:
        payload.update(extra)
    return payload
