from __future__ import annotations

import re
from typing import Any

from rag_helper.extractors.java_ast_helpers import make_relation
from rag_helper.extractors.csharp_type_resolution import uniq_keep_order
from rag_helper.utils.text_normalization import normalize_ws


def node_text(src: bytes, node) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")


def first_child_of_type(node, typ: str):
    for child in node.children:
        if child.type == typ:
            return child
    return None


def children_of_type(node, typ: str) -> list:
    return [child for child in node.children if child.type == typ]


def extract_namespace(root, src: bytes) -> str | None:
    for child in root.children:
        if child.type == "namespace_declaration":
            name = child.child_by_field_name("name")
            return node_text(src, name).strip() if name else None
        if child.type == "file_scoped_namespace_declaration":
            name = child.child_by_field_name("name")
            return node_text(src, name).strip() if name else None
    return None


def extract_usings(root, src: bytes) -> list[str]:
    usings: list[str] = []
    for child in root.children:
        if child.type != "using_directive":
            continue
        text = normalize_ws(node_text(src, child)).removeprefix("using ").rstrip(";").strip()
        if text.startswith("global "):
            text = text.removeprefix("global ").strip()
        if text.startswith("static "):
            text = f"static:{text.removeprefix('static ').strip()}"
        usings.append(text)
    return usings


def extract_modifiers(node, src: bytes) -> list[str]:
    return [node_text(src, child).strip() for child in node.children if child.type == "modifier"]


def extract_attributes(node, src: bytes) -> list[str]:
    attributes: list[str] = []
    for child in node.children:
        if child.type != "attribute_list":
            continue
        for nested in child.children:
            if nested.type == "attribute":
                attributes.append(node_text(src, nested).strip())
    return attributes


def extract_identifier(node, src: bytes) -> str | None:
    ident = node.child_by_field_name("name")
    if ident is not None:
        return node_text(src, ident).strip()
    for child in node.children:
        if child.type == "identifier":
            return node_text(src, child).strip()
    return None


def extract_type_node_text(node, src: bytes, field_name: str = "type") -> str | None:
    type_node = node.child_by_field_name(field_name)
    return normalize_ws(node_text(src, type_node)) if type_node is not None else None


def extract_parameter_list(node, src: bytes) -> list[str]:
    parameters_node = node.child_by_field_name("parameters")
    if parameters_node is None:
        parameters_node = first_child_of_type(node, "parameter_list")
    if parameters_node is None:
        return []
    params: list[str] = []
    for child in parameters_node.children:
        if child.type in {"parameter", "variable_declarator"}:
            params.append(normalize_ws(node_text(src, child)))
    return params


def extract_base_types(node, src: bytes) -> tuple[str | None, list[str]]:
    base_list = first_child_of_type(node, "base_list")
    if base_list is None:
        return None, []
    base_types: list[str] = []
    for child in base_list.children:
        if child.type in {
            "identifier",
            "qualified_name",
            "generic_name",
            "predefined_type",
            "nullable_type",
            "alias_qualified_name",
        }:
            base_types.append(normalize_ws(node_text(src, child)))
        elif child.type == "primary_constructor_base_type":
            type_node = child.child_by_field_name("type")
            if type_node is not None:
                base_types.append(normalize_ws(node_text(src, type_node)))
    if not base_types:
        return None, []
    return base_types[0], base_types[1:]


def extract_field(node, src: bytes) -> list[dict[str, Any]]:
    variable_declaration = first_child_of_type(node, "variable_declaration")
    if variable_declaration is None:
        return []
    field_type = extract_type_node_text(variable_declaration, src)
    modifiers = extract_modifiers(node, src)
    attributes = extract_attributes(node, src)
    documentation = extract_xml_documentation(node, src)
    fields: list[dict[str, Any]] = []
    for declarator in children_of_type(variable_declaration, "variable_declarator"):
        name_node = declarator.child_by_field_name("name")
        initializer = declarator.child_by_field_name("value")
        name = node_text(src, name_node).strip() if name_node is not None else normalize_ws(node_text(src, declarator))
        fields.append({
            "name": name,
            "type": field_type,
            "initializer": normalize_ws(node_text(src, initializer)) if initializer is not None else None,
            "modifiers": modifiers,
            "attributes": attributes,
            "documentation": documentation,
            "documentation_summary": extract_xml_documentation_summary(documentation),
        })
    return fields


def extract_property(node, src: bytes) -> dict[str, Any]:
    accessors = first_child_of_type(node, "accessor_list")
    accessor_names: list[str] = []
    has_accessor_body = False
    if accessors is not None:
        for accessor in children_of_type(accessors, "accessor_declaration"):
            name = accessor.child_by_field_name("name")
            if name is not None:
                accessor_names.append(node_text(src, name).strip())
            if accessor.child_by_field_name("body") is not None:
                has_accessor_body = True
    return {
        "name": extract_identifier(node, src),
        "property_type": extract_type_node_text(node, src),
        "modifiers": extract_modifiers(node, src),
        "attributes": extract_attributes(node, src),
        "documentation": extract_xml_documentation(node, src),
        "documentation_summary": extract_xml_documentation_summary(extract_xml_documentation(node, src)),
        "accessors": accessor_names,
        "is_auto_property": bool(accessor_names) and not has_accessor_body,
        "is_trivial": bool(accessor_names) and not has_accessor_body,
    }


def extract_method_calls(node, src: bytes) -> list[str]:
    if node is None:
        return []
    calls: list[str] = []
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type == "invocation_expression":
            calls.append(normalize_ws(node_text(src, current)))
        stack.extend(reversed(current.children))
    return uniq_keep_order([call[:250] for call in calls], limit=200)


def extract_type_refs(node, src: bytes) -> list[str]:
    refs: list[str] = []
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type in {
            "alias_qualified_name",
            "array_type",
            "function_pointer_type",
            "generic_name",
            "nullable_type",
            "pointer_type",
            "predefined_type",
            "qualified_name",
            "ref_type",
            "scoped_type",
            "tuple_type",
        }:
            refs.append(normalize_ws(node_text(src, current)))
        stack.extend(reversed(current.children))
    return uniq_keep_order(refs, limit=200)


def extract_xml_documentation(node, src: bytes) -> str | None:
    start_point = getattr(node, "start_point", None)
    if not isinstance(start_point, tuple) or len(start_point) < 1:
        return None

    lines = src.decode("utf-8", errors="ignore").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    line_index = max(0, start_point[0] - 1)
    doc_lines: list[str] = []
    while line_index >= 0:
        stripped = lines[line_index].strip()
        if not stripped:
            if doc_lines:
                break
            line_index -= 1
            continue
        if stripped.startswith("///"):
            doc_lines.append(stripped.removeprefix("///").strip())
            line_index -= 1
            continue
        break
    if not doc_lines:
        return None
    return _clean_xml_documentation("\n".join(reversed(doc_lines)))


def extract_xml_documentation_summary(documentation: str | None) -> str | None:
    if not documentation:
        return None
    text = re.sub(r"<[^>]+>", " ", documentation)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None
    match = re.search(r"(.+?[.!?])(?:\s|$)", text)
    if match:
        return match.group(1).strip()
    return text[:220].rstrip()


def _clean_xml_documentation(raw: str) -> str:
    text = raw
    text = re.sub(r"</?(summary|remarks|returns|example|value|para)>", "\n", text)
    text = re.sub(r"<(param|typeparam)\s+name=\"([^\"]+)\">", r"\n@\1 \2 ", text)
    text = re.sub(r"<see\s+cref=\"([^\"]+)\"\s*/>", r"\1", text)
    text = re.sub(r"<code>", "\n<code>", text)
    text = re.sub(r"</code>", "</code>\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.split("\n")]
    compacted: list[str] = []
    previous_blank = False
    for line in lines:
        if not line:
            if compacted and not previous_blank:
                compacted.append("")
            previous_blank = True
            continue
        compacted.append(line)
        previous_blank = False
    while compacted and compacted[-1] == "":
        compacted.pop()
    return "\n".join(compacted).strip()
