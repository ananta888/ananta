from __future__ import annotations

from dataclasses import dataclass
import re

from rag_helper.domain.csharp_records import (
    CSharpConstructorDetailRecord,
    CSharpConstructorRecord,
    CSharpMethodDetailRecord,
    CSharpMethodRecord,
    CSharpPropertyRecord,
    RelationRecord,
)
from rag_helper.extractors.csharp_ast_helpers import (
    children_of_type,
    extract_attributes,
    extract_identifier,
    extract_method_calls,
    extract_modifiers,
    extract_parameter_list,
    extract_property,
    extract_type_node_text,
    extract_type_refs,
    extract_xml_documentation,
    extract_xml_documentation_summary,
    make_relation,
    node_text,
)
from rag_helper.extractors.csharp_type_resolution import (
    find_resolution_conflicts,
    resolve_type_name,
    uniq_conflicts,
    uniq_keep_order,
)
from rag_helper.utils.embedding_text import build_embedding_text, compact_list
from rag_helper.utils.ids import safe_id
from rag_helper.utils.text_normalization import compact_code_snippet


def looks_like_getter(name: str, params: list[str], return_type: str | None) -> bool:
    return name.startswith("Get") and len(params) == 0 and return_type not in (None, "void")


def looks_like_setter(name: str, params: list[str], return_type: str | None) -> bool:
    return name.startswith("Set") and len(params) == 1 and (return_type is None or return_type == "void")


@dataclass(frozen=True)
class CSharpMemberContext:
    rel_path: str
    src: bytes
    namespace_name: str | None
    using_map: dict[str, str]
    using_namespaces: list[str]
    known_namespace_types: dict[str, set[str]]
    same_file_types: set[str]
    include_code_snippets: bool
    relation_mode: str
    embedding_text_mode: str
    mark_import_conflicts: bool
    resolve_method_targets: bool
    field_type_lookup: dict[str, list[str]]


def extract_method(
    ctx: CSharpMemberContext,
    class_name: str,
    parent_type_id: str,
    node,
) -> tuple[CSharpMethodRecord, CSharpMethodDetailRecord, list[RelationRecord], dict[str, bool]]:
    name = extract_identifier(node, ctx.src) or "<method>"
    modifiers = extract_modifiers(node, ctx.src)
    attributes = extract_attributes(node, ctx.src)
    parameters = extract_parameter_list(node, ctx.src)
    return_type = extract_type_node_text(node, ctx.src, "returns")
    body = node.child_by_field_name("body")

    signature = f"{name}({', '.join(parameters)})"
    if return_type:
        signature += f": {return_type}"

    calls = extract_method_calls(body, ctx.src)
    type_refs = extract_type_refs(node, ctx.src)
    resolved_type_refs: list[str] = []
    resolution_conflicts: list[dict[str, object]] = []
    for type_ref in type_refs:
        resolved = resolve_type_name(
            type_ref,
            ctx.namespace_name,
            ctx.using_map,
            ctx.known_namespace_types,
            ctx.same_file_types,
            using_namespaces=ctx.using_namespaces,
        )
        resolved_type_refs.extend(resolved)
        if ctx.mark_import_conflicts:
            resolution_conflicts.extend(find_resolution_conflicts(type_ref, resolved))
    resolved_type_refs = uniq_keep_order(resolved_type_refs, limit=60)

    is_getter = looks_like_getter(name, parameters, return_type)
    is_setter = looks_like_setter(name, parameters, return_type)
    is_trivial = is_getter or is_setter

    method_id = f"cs_method:{safe_id(ctx.rel_path, class_name, signature)}"
    documentation = extract_xml_documentation(node, ctx.src)
    documentation_summary = extract_xml_documentation_summary(documentation)
    resolved_return_types = resolve_type_name(
        return_type,
        ctx.namespace_name,
        ctx.using_map,
        ctx.known_namespace_types,
        ctx.same_file_types,
        using_namespaces=ctx.using_namespaces,
    )
    if ctx.mark_import_conflicts:
        resolution_conflicts.extend(find_resolution_conflicts(return_type or "", resolved_return_types))
    resolution_conflicts = uniq_conflicts(resolution_conflicts)
    resolved_call_targets = resolve_call_targets(
        calls=calls,
        class_name=class_name,
        namespace_name=ctx.namespace_name,
        field_type_lookup=ctx.field_type_lookup,
        parameter_bindings=parse_parameter_bindings(parameters),
        same_file_types=ctx.same_file_types,
        resolve_enabled=ctx.resolve_method_targets,
    )
    embedding_text = build_embedding_text(
        ctx.embedding_text_mode,
        (
            f"CSharp method {name} in class {class_name}. "
            f"Signature {signature}. "
            f"Modifiers: {', '.join(modifiers) or 'none'}. "
            f"Attributes: {', '.join(attributes) or 'none'}. "
            f"Documentation: {documentation_summary or 'none'}. "
            f"Calls: {', '.join(calls[:20]) or 'none'}. "
            f"Uses resolved types: {', '.join(resolved_type_refs[:20]) or 'none'}. "
            f"Trivial accessor: {'yes' if is_trivial else 'no'}."
        ),
        (
            f"CSharp method {class_name}.{name}. "
            f"Signature {signature}. "
            f"Doc {documentation_summary or 'none'}. "
            f"Calls {compact_list(calls, limit=6)}. "
            f"Types {compact_list(resolved_type_refs, limit=6)}."
        ),
    )

    idx: CSharpMethodRecord = {
        "kind": "cs_method",
        "file": ctx.rel_path,
        "id": method_id,
        "parent_id": parent_type_id,
        "class": class_name,
        "name": name,
        "signature": signature,
        "return_type": return_type,
        "resolved_return_types": resolved_return_types,
        "parameters": parameters,
        "modifiers": modifiers,
        "attributes": attributes,
        "documentation": documentation,
        "documentation_summary": documentation_summary,
        "parameter_count": len(parameters),
        "calls": calls[:30],
        "type_refs": type_refs[:30],
        "resolved_type_refs": resolved_type_refs,
        "is_getter": is_getter,
        "is_setter": is_setter,
        "is_trivial": is_trivial,
        "type_resolution_conflicts": resolution_conflicts,
        "resolved_call_targets": resolved_call_targets,
        "embedding_text": embedding_text,
    }

    detail: CSharpMethodDetailRecord = {
        "kind": "cs_method_detail",
        "file": ctx.rel_path,
        "id": f"cs_method_detail:{safe_id(ctx.rel_path, class_name, signature)}",
        "parent_id": method_id,
        "class": class_name,
        "name": name,
        "signature": signature,
        "return_type": return_type,
        "resolved_return_types": resolved_return_types,
        "parameters": parameters,
        "modifiers": modifiers,
        "attributes": attributes,
        "documentation": documentation,
        "documentation_summary": documentation_summary,
        "calls": calls,
        "type_refs": type_refs,
        "resolved_type_refs": resolved_type_refs,
        "is_getter": is_getter,
        "is_setter": is_setter,
        "is_trivial": is_trivial,
        "type_resolution_conflicts": resolution_conflicts,
        "resolved_call_targets": resolved_call_targets,
        "embedding_text": embedding_text,
    }
    if ctx.include_code_snippets:
        detail["code_snippet"] = compact_code_snippet(node_text(ctx.src, node), max_len=3200)

    relations: list[RelationRecord] = []
    for raw_type in type_refs:
        for resolved_target in resolve_type_name(
            raw_type,
            ctx.namespace_name,
            ctx.using_map,
            ctx.known_namespace_types,
            ctx.same_file_types,
            using_namespaces=ctx.using_namespaces,
        ):
            relations.append(make_relation(
                file=ctx.rel_path,
                source_id=method_id,
                source_kind="cs_method",
                source_name=f"{class_name}.{name}",
                relation="uses_type",
                target=raw_type,
                target_resolved=resolved_target,
            ))
    if return_type:
        for resolved_target in resolved_return_types or [return_type]:
            relations.append(make_relation(
                file=ctx.rel_path,
                source_id=method_id,
                source_kind="cs_method",
                source_name=f"{class_name}.{name}",
                relation="returns",
                target=return_type,
                target_resolved=resolved_target,
            ))
    if ctx.relation_mode != "compact":
        for call in calls:
            relations.append(make_relation(
                file=ctx.rel_path,
                source_id=method_id,
                source_kind="cs_method",
                source_name=f"{class_name}.{name}",
                relation="calls",
                target=call,
                target_resolved=None,
            ))
    for call_target in resolved_call_targets:
        relations.append(make_relation(
            file=ctx.rel_path,
            source_id=method_id,
            source_kind="cs_method",
            source_name=f"{class_name}.{name}",
            relation="calls_probable_target",
            target=call_target["call"],
            target_resolved=call_target["target_resolved"],
            extra={
                "confidence": call_target["confidence"],
                "heuristic": call_target["heuristic"],
            },
        ))
    return idx, detail, relations, {"is_trivial": is_trivial}


def extract_property_record(
    ctx: CSharpMemberContext,
    class_name: str,
    parent_type_id: str,
    node,
) -> CSharpPropertyRecord:
    property_data = extract_property(node, ctx.src)
    property_type = property_data.get("property_type")
    resolved_property_types = resolve_type_name(
        property_type,
        ctx.namespace_name,
        ctx.using_map,
        ctx.known_namespace_types,
        ctx.same_file_types,
        using_namespaces=ctx.using_namespaces,
    )
    property_id = f"cs_property:{safe_id(ctx.rel_path, class_name, property_data.get('name') or '')}"
    embedding_text = build_embedding_text(
        ctx.embedding_text_mode,
        (
            f"CSharp property {property_data.get('name')} in class {class_name}. "
            f"Type {property_type or 'unknown'}. "
            f"Accessors: {', '.join(property_data.get('accessors', [])) or 'none'}. "
            f"Documentation: {property_data.get('documentation_summary') or 'none'}."
        ),
        (
            f"CSharp property {class_name}.{property_data.get('name')}. "
            f"Type {property_type or 'unknown'}. "
            f"Accessors {compact_list(property_data.get('accessors', []), limit=4)}."
        ),
    )
    return {
        "kind": "cs_property",
        "file": ctx.rel_path,
        "id": property_id,
        "parent_id": parent_type_id,
        "class": class_name,
        "name": property_data.get("name") or "<property>",
        "property_type": property_type,
        "resolved_property_types": resolved_property_types,
        "modifiers": property_data.get("modifiers", []),
        "attributes": property_data.get("attributes", []),
        "documentation": property_data.get("documentation"),
        "documentation_summary": property_data.get("documentation_summary"),
        "accessors": property_data.get("accessors", []),
        "is_auto_property": bool(property_data.get("is_auto_property")),
        "is_trivial": bool(property_data.get("is_trivial")),
        "embedding_text": embedding_text,
    }


def extract_constructor(
    ctx: CSharpMemberContext,
    class_name: str,
    parent_type_id: str,
    node,
) -> tuple[CSharpConstructorRecord, CSharpConstructorDetailRecord, list[RelationRecord]]:
    name = extract_identifier(node, ctx.src) or class_name
    modifiers = extract_modifiers(node, ctx.src)
    attributes = extract_attributes(node, ctx.src)
    documentation = extract_xml_documentation(node, ctx.src)
    documentation_summary = extract_xml_documentation_summary(documentation)
    parameters = extract_parameter_list(node, ctx.src)
    body = node.child_by_field_name("body")

    signature = f"{name}({', '.join(parameters)})"
    calls = extract_method_calls(body, ctx.src)
    type_refs = extract_type_refs(node, ctx.src)
    resolved_type_refs: list[str] = []
    resolution_conflicts: list[dict[str, object]] = []
    for type_ref in type_refs:
        resolved = resolve_type_name(
            type_ref,
            ctx.namespace_name,
            ctx.using_map,
            ctx.known_namespace_types,
            ctx.same_file_types,
            using_namespaces=ctx.using_namespaces,
        )
        resolved_type_refs.extend(resolved)
        if ctx.mark_import_conflicts:
            resolution_conflicts.extend(find_resolution_conflicts(type_ref, resolved))
    resolved_type_refs = uniq_keep_order(resolved_type_refs, limit=60)
    resolution_conflicts = uniq_conflicts(resolution_conflicts)
    resolved_call_targets = resolve_call_targets(
        calls=calls,
        class_name=class_name,
        namespace_name=ctx.namespace_name,
        field_type_lookup=ctx.field_type_lookup,
        parameter_bindings=parse_parameter_bindings(parameters),
        same_file_types=ctx.same_file_types,
        resolve_enabled=ctx.resolve_method_targets,
    )
    ctor_id = f"cs_constructor:{safe_id(ctx.rel_path, class_name, signature)}"
    embedding_text = build_embedding_text(
        ctx.embedding_text_mode,
        (
            f"CSharp constructor {name} in class {class_name}. "
            f"Signature {signature}. "
            f"Modifiers: {', '.join(modifiers) or 'none'}. "
            f"Documentation: {documentation_summary or 'none'}. "
            f"Calls: {', '.join(calls[:20]) or 'none'}. "
            f"Uses resolved types: {', '.join(resolved_type_refs[:20]) or 'none'}."
        ),
        (
            f"CSharp constructor {class_name}.{name}. "
            f"Signature {signature}. "
            f"Doc {documentation_summary or 'none'}. "
            f"Calls {compact_list(calls, limit=6)}. "
            f"Types {compact_list(resolved_type_refs, limit=6)}."
        ),
    )

    idx: CSharpConstructorRecord = {
        "kind": "cs_constructor",
        "file": ctx.rel_path,
        "id": ctor_id,
        "parent_id": parent_type_id,
        "class": class_name,
        "name": name,
        "signature": signature,
        "parameters": parameters,
        "modifiers": modifiers,
        "attributes": attributes,
        "documentation": documentation,
        "documentation_summary": documentation_summary,
        "parameter_count": len(parameters),
        "calls": calls[:30],
        "type_refs": type_refs[:30],
        "resolved_type_refs": resolved_type_refs,
        "type_resolution_conflicts": resolution_conflicts,
        "resolved_call_targets": resolved_call_targets,
        "embedding_text": embedding_text,
    }

    detail: CSharpConstructorDetailRecord = {
        "kind": "cs_constructor_detail",
        "file": ctx.rel_path,
        "id": f"cs_constructor_detail:{safe_id(ctx.rel_path, class_name, signature)}",
        "parent_id": ctor_id,
        "class": class_name,
        "name": name,
        "signature": signature,
        "parameters": parameters,
        "modifiers": modifiers,
        "attributes": attributes,
        "documentation": documentation,
        "documentation_summary": documentation_summary,
        "calls": calls,
        "type_refs": type_refs,
        "resolved_type_refs": resolved_type_refs,
        "type_resolution_conflicts": resolution_conflicts,
        "resolved_call_targets": resolved_call_targets,
        "embedding_text": embedding_text,
    }
    if ctx.include_code_snippets:
        detail["code_snippet"] = compact_code_snippet(node_text(ctx.src, node), max_len=3200)

    relations: list[RelationRecord] = []
    for raw_type in type_refs:
        for resolved_target in resolve_type_name(
            raw_type,
            ctx.namespace_name,
            ctx.using_map,
            ctx.known_namespace_types,
            ctx.same_file_types,
            using_namespaces=ctx.using_namespaces,
        ):
            relations.append(make_relation(
                file=ctx.rel_path,
                source_id=ctor_id,
                source_kind="cs_constructor",
                source_name=f"{class_name}.{name}",
                relation="uses_type",
                target=raw_type,
                target_resolved=resolved_target,
            ))
    if ctx.relation_mode != "compact":
        for call in calls:
            relations.append(make_relation(
                file=ctx.rel_path,
                source_id=ctor_id,
                source_kind="cs_constructor",
                source_name=f"{class_name}.{name}",
                relation="calls",
                target=call,
                target_resolved=None,
            ))
    for call_target in resolved_call_targets:
        relations.append(make_relation(
            file=ctx.rel_path,
            source_id=ctor_id,
            source_kind="cs_constructor",
            source_name=f"{class_name}.{name}",
            relation="calls_probable_target",
            target=call_target["call"],
            target_resolved=call_target["target_resolved"],
            extra={
                "confidence": call_target["confidence"],
                "heuristic": call_target["heuristic"],
            },
        ))
    return idx, detail, relations


CALL_PATTERN = re.compile(r"(?:(?P<qualifier>[A-Za-z_]\w*)\s*\.)?\s*(?P<method>[A-Za-z_]\w*)\s*\(")
PARAM_PATTERN = re.compile(r"(?P<type>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*(?:<[^>]+>)?(?:\[\])?)\s+(?P<name>[A-Za-z_]\w*)$")


def parse_parameter_bindings(parameters: list[str]) -> dict[str, str]:
    bindings: dict[str, str] = {}
    for parameter in parameters:
        cleaned = " ".join(part for part in parameter.split() if part not in {"ref", "out", "in", "params", "scoped", "readonly"})
        match = PARAM_PATTERN.search(cleaned.replace("...", "[]"))
        if match:
            bindings[match.group("name")] = match.group("type")
    return bindings


def resolve_call_targets(
    calls: list[str],
    class_name: str,
    namespace_name: str | None,
    field_type_lookup: dict[str, list[str]],
    parameter_bindings: dict[str, str],
    same_file_types: set[str],
    resolve_enabled: bool,
) -> list[dict[str, str]]:
    if not resolve_enabled:
        return []

    targets: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for call in calls:
        match = CALL_PATTERN.search(call)
        if not match:
            continue
        qualifier = match.group("qualifier")
        method_name = match.group("method")
        heuristic = "unqualified_same_class"
        candidates: list[str] = []

        if qualifier and qualifier in field_type_lookup:
            heuristic = "field_type"
            candidates = [f"{resolved_type}.{method_name}" for resolved_type in field_type_lookup[qualifier]]
        elif qualifier and qualifier in parameter_bindings:
            heuristic = "parameter_type"
            candidates = [f"{parameter_bindings[qualifier]}.{method_name}"]
        elif qualifier and qualifier[:1].isupper():
            heuristic = "qualifier_class_name"
            resolved_class = f"{namespace_name}.{qualifier}" if namespace_name and qualifier in same_file_types else qualifier
            candidates = [f"{resolved_class}.{method_name}"]
        elif not qualifier:
            resolved_class = f"{namespace_name}.{class_name}" if namespace_name else class_name
            candidates = [f"{resolved_class}.{method_name}"]

        confidence = "medium" if heuristic in {"field_type", "parameter_type"} else "low"
        for candidate in candidates:
            key = (call, candidate)
            if key in seen:
                continue
            seen.add(key)
            targets.append({
                "call": call,
                "target_resolved": candidate,
                "confidence": confidence,
                "heuristic": heuristic,
            })
    return targets
