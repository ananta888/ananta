from __future__ import annotations

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
