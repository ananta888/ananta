from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rag_helper.domain.csharp_records import CSharpTypeRecord, RelationRecord
from rag_helper.extractors.csharp_ast_helpers import (
    extract_attributes,
    extract_base_types,
    extract_field,
    extract_identifier,
    extract_modifiers,
    extract_type_node_text,
    extract_xml_documentation,
    extract_xml_documentation_summary,
    first_child_of_type,
    make_relation,
)
from rag_helper.extractors.csharp_member_extractor import (
    CSharpMemberContext,
    extract_constructor,
    extract_method,
    extract_property_record,
)
from rag_helper.extractors.csharp_role_detection import detect_type_roles
from rag_helper.extractors.csharp_type_resolution import (
    find_resolution_conflicts,
    resolve_type_name,
    split_generics,
    uniq_conflicts,
    uniq_keep_order,
)
from rag_helper.utils.embedding_text import build_embedding_text, compact_list
from rag_helper.utils.ids import safe_id


@dataclass(frozen=True)
class CSharpTypeContext:
    rel_path: str
    src: bytes
    namespace_name: str | None
    usings: list[str]
    using_map: dict[str, str]
    using_namespaces: list[str]
    known_namespace_types: dict[str, set[str]]
    same_file_types: set[str]
    include_code_snippets: bool
    exclude_trivial_methods: bool
    max_methods_per_class: int | None
    detail_mode: str
    relation_mode: str
    mark_import_conflicts: bool
    resolve_method_targets: bool
    embedding_text_mode: str = "verbose"


def extract_type(
    ctx: CSharpTypeContext,
    node,
) -> tuple[CSharpTypeRecord, list[dict[str, Any]], list[RelationRecord], dict[str, int]]:
    name = extract_identifier(node, ctx.src) or "<anonymous>"
    type_kind = node.type.replace("_declaration", "")
    modifiers = extract_modifiers(node, ctx.src)
    attributes = extract_attributes(node, ctx.src)
    documentation = extract_xml_documentation(node, ctx.src)
    documentation_summary = extract_xml_documentation_summary(documentation)
    extends, implements = extract_base_types(node, ctx.src)

    body = node.child_by_field_name("body")

    fields: list[dict[str, Any]] = []
    property_names: list[str] = []
    method_signatures: list[str] = []
    constructor_signatures: list[str] = []
    detail_records: list[dict[str, Any]] = []
    relation_records: list[RelationRecord] = []
    method_indexes: list[dict[str, Any]] = []
    property_indexes: list[dict[str, Any]] = []
    field_type_resolved: list[str] = []
    called_methods: list[str] = []
    used_types_resolved: list[str] = []
    skipped_method_count = 0
    type_resolution_conflicts: list[dict[str, object]] = []

    type_id = f"cs_type:{safe_id(ctx.rel_path, name, type_kind)}"
    file_id = f"cs_file:{safe_id(ctx.rel_path)}"
    if body:
        field_type_lookup: dict[str, list[str]] = {}
        for member in body.children:
            if member.type != "field_declaration":
                continue
            for field in extract_field(member, ctx.src):
                resolved_field_types = resolve_type_name(
                    field.get("type"),
                    namespace_name=ctx.namespace_name,
                    using_map=ctx.using_map,
                    known_namespace_types=ctx.known_namespace_types,
                    same_file_types=ctx.same_file_types,
                    using_namespaces=ctx.using_namespaces,
                )
                if field.get("name"):
                    field_type_lookup[str(field.get("name"))] = resolved_field_types

        member_ctx = CSharpMemberContext(
            rel_path=ctx.rel_path,
            src=ctx.src,
            namespace_name=ctx.namespace_name,
            using_map=ctx.using_map,
            using_namespaces=ctx.using_namespaces,
            known_namespace_types=ctx.known_namespace_types,
            same_file_types=ctx.same_file_types,
            include_code_snippets=ctx.include_code_snippets,
            relation_mode=ctx.relation_mode,
            embedding_text_mode=ctx.embedding_text_mode,
            mark_import_conflicts=ctx.mark_import_conflicts,
            resolve_method_targets=ctx.resolve_method_targets,
            field_type_lookup=field_type_lookup,
        )

        for member in body.children:
            if member.type == "field_declaration":
                for field in extract_field(member, ctx.src):
                    field["resolved_types"] = resolve_type_name(
                        field.get("type"),
                        namespace_name=ctx.namespace_name,
                        using_map=ctx.using_map,
                        known_namespace_types=ctx.known_namespace_types,
                        same_file_types=ctx.same_file_types,
                        using_namespaces=ctx.using_namespaces,
                    )
                    if ctx.mark_import_conflicts:
                        type_resolution_conflicts.extend(
                            find_resolution_conflicts(field.get("type") or "", field["resolved_types"])
                        )
                    fields.append(field)
                    field_type_resolved.extend(field["resolved_types"])
                    for raw_type in split_generics(field.get("type") or ""):
                        for resolved_target in resolve_type_name(
                            raw_type,
                            ctx.namespace_name,
                            ctx.using_map,
                            ctx.known_namespace_types,
                            ctx.same_file_types,
                            using_namespaces=ctx.using_namespaces,
                        ):
                            relation_records.append(make_relation(
                                file=ctx.rel_path,
                                source_id=type_id,
                                source_kind="cs_type",
                                source_name=name,
                                relation="field_type_uses",
                                target=raw_type,
                                target_resolved=resolved_target,
                            ))

            elif member.type == "property_declaration":
                property_record = extract_property_record(member_ctx, name, type_id, member)
                property_names.append(f"{property_record['name']}: {property_record.get('property_type') or 'unknown'}")
                property_indexes.append(property_record)
                detail_records.append(property_record)
                used_types_resolved.extend(property_record.get("resolved_property_types", []))
                for resolved_target in property_record.get("resolved_property_types", []) or [property_record.get("property_type")]:
                    if not resolved_target:
                        continue
                    relation_records.append(make_relation(
                        file=ctx.rel_path,
                        source_id=type_id,
                        source_kind="cs_type",
                        source_name=name,
                        relation="declares_property",
                        target=property_record["name"],
                        target_resolved=property_record["id"],
                    ))
                    relation_records.append(make_relation(
                        file=ctx.rel_path,
                        source_id=property_record["id"],
                        source_kind="cs_property",
                        source_name=f"{name}.{property_record['name']}",
                        relation="property_type_uses",
                        target=property_record.get("property_type") or "",
                        target_resolved=resolved_target,
                    ))
                    relation_records.append(make_relation(
                        file=ctx.rel_path,
                        source_id=property_record["id"],
                        source_kind="cs_property",
                        source_name=f"{name}.{property_record['name']}",
                        relation="child_of_type",
                        target=name,
                        target_resolved=type_id,
                    ))

            elif member.type == "method_declaration":
                method_index, method_detail, method_relations, method_meta = extract_method(member_ctx, name, type_id, member)
                if ctx.exclude_trivial_methods and method_meta["is_trivial"]:
                    continue
                if ctx.max_methods_per_class is not None and len(method_signatures) >= ctx.max_methods_per_class:
                    skipped_method_count += 1
                    continue
                method_signatures.append(method_index["signature"])
                called_methods.extend(method_index["calls"])
                used_types_resolved.extend(method_index["resolved_type_refs"])
                method_indexes.append(method_index)
                detail_records.append(method_index)
                if ctx.detail_mode != "compact":
                    detail_records.append(method_detail)
                relation_records.extend(method_relations)
                if ctx.mark_import_conflicts:
                    type_resolution_conflicts.extend(method_index.get("type_resolution_conflicts", []))
                if ctx.relation_mode != "compact":
                    relation_records.append(make_relation(
                        file=ctx.rel_path,
                        source_id=type_id,
                        source_kind="cs_type",
                        source_name=name,
                        relation="declares_method",
                        target=method_index["name"],
                        target_resolved=method_index["id"],
                    ))
                    relation_records.append(make_relation(
                        file=ctx.rel_path,
                        source_id=method_index["id"],
                        source_kind="cs_method",
                        source_name=f"{name}.{method_index['name']}",
                        relation="child_of_type",
                        target=name,
                        target_resolved=type_id,
                    ))

            elif member.type == "constructor_declaration":
                constructor_index, constructor_detail, constructor_relations = extract_constructor(member_ctx, name, type_id, member)
                constructor_signatures.append(constructor_index["signature"])
                called_methods.extend(constructor_index["calls"])
                used_types_resolved.extend(constructor_index["resolved_type_refs"])
                detail_records.append(constructor_index)
                if ctx.detail_mode != "compact":
                    detail_records.append(constructor_detail)
                relation_records.extend(constructor_relations)
                if ctx.mark_import_conflicts:
                    type_resolution_conflicts.extend(constructor_index.get("type_resolution_conflicts", []))
                if ctx.relation_mode != "compact":
                    relation_records.append(make_relation(
                        file=ctx.rel_path,
                        source_id=type_id,
                        source_kind="cs_type",
                        source_name=name,
                        relation="declares_constructor",
                        target=constructor_index["signature"],
                        target_resolved=constructor_index["id"],
                    ))
                    relation_records.append(make_relation(
                        file=ctx.rel_path,
                        source_id=constructor_index["id"],
                        source_kind="cs_constructor",
                        source_name=f"{name}.{constructor_index['name']}",
                        relation="child_of_type",
                        target=name,
                        target_resolved=type_id,
                    ))

    roles = detect_type_roles(
        type_name=name,
        type_kind=type_kind,
        attributes=attributes,
        usings=ctx.usings,
        fields=fields,
        methods=method_indexes,
        properties=property_indexes,
    )

    extends_resolved = resolve_type_name(
        extends,
        ctx.namespace_name,
        ctx.using_map,
        ctx.known_namespace_types,
        ctx.same_file_types,
        using_namespaces=ctx.using_namespaces,
    )
    if ctx.mark_import_conflicts:
        type_resolution_conflicts.extend(find_resolution_conflicts(extends or "", extends_resolved))
    implements_resolved: list[str] = []
    for interface_name in implements:
        resolved_interface = resolve_type_name(
            interface_name,
            ctx.namespace_name,
            ctx.using_map,
            ctx.known_namespace_types,
            ctx.same_file_types,
            using_namespaces=ctx.using_namespaces,
        )
        implements_resolved.extend(resolved_interface)
        if ctx.mark_import_conflicts:
            type_resolution_conflicts.extend(find_resolution_conflicts(interface_name, resolved_interface))

    used_types_resolved = uniq_keep_order(
        used_types_resolved + field_type_resolved + extends_resolved + implements_resolved,
        limit=100,
    )
    called_methods = uniq_keep_order(called_methods, limit=50)

    if extends:
        for resolved_target in extends_resolved or [extends]:
            relation_records.append(make_relation(
                file=ctx.rel_path,
                source_id=type_id,
                source_kind="cs_type",
                source_name=name,
                relation="extends",
                target=extends,
                target_resolved=resolved_target,
            ))
    for interface_name in implements:
        resolved_interfaces = resolve_type_name(
            interface_name,
            ctx.namespace_name,
            ctx.using_map,
            ctx.known_namespace_types,
            ctx.same_file_types,
            using_namespaces=ctx.using_namespaces,
        )
        for resolved_target in resolved_interfaces or [interface_name]:
            relation_records.append(make_relation(
                file=ctx.rel_path,
                source_id=type_id,
                source_kind="cs_type",
                source_name=name,
                relation="implements",
                target=interface_name,
                target_resolved=resolved_target,
            ))

    embedding_text = build_embedding_text(
        ctx.embedding_text_mode,
        (
            f"CSharp {type_kind} {name} in file {ctx.rel_path}. "
            f"Namespace {ctx.namespace_name or 'global'}. "
            f"Roles: {', '.join(roles['role_labels']) or 'none'}. "
            f"Modifiers: {', '.join(modifiers) or 'none'}. "
            f"Attributes: {', '.join(attributes) or 'none'}. "
            f"Documentation: {documentation_summary or 'none'}. "
            f"Extends {extends or 'none'}. Implements {', '.join(implements) or 'none'}. "
            f"Properties: {', '.join(property_names[:20]) or 'none'}. "
            f"Methods: {', '.join(method_signatures[:20]) or 'none'}. "
            f"Used types: {', '.join(used_types_resolved[:20]) or 'none'}. "
            f"Calls: {', '.join(called_methods[:20]) or 'none'}."
        ),
        (
            f"CSharp {type_kind} {name}. Namespace {ctx.namespace_name or 'global'}. "
            f"Roles {compact_list(roles['role_labels'], limit=4)}. "
            f"Doc {documentation_summary or 'none'}. "
            f"Properties {compact_list(property_names, limit=6)}. "
            f"Methods {compact_list(method_signatures, limit=6)}."
        ),
    )

    type_record: CSharpTypeRecord = {
        "kind": "cs_type",
        "file": ctx.rel_path,
        "id": type_id,
        "parent_id": file_id,
        "namespace": ctx.namespace_name,
        "usings": ctx.usings,
        "name": name,
        "type_kind": type_kind,
        "modifiers": modifiers,
        "attributes": attributes,
        "documentation": documentation,
        "documentation_summary": documentation_summary,
        "extends": extends,
        "extends_resolved": extends_resolved,
        "implements": implements,
        "implements_resolved": implements_resolved,
        "fields": fields[:50],
        "properties": property_names[:100],
        "methods": method_signatures[:200],
        "constructors": constructor_signatures[:50],
        "used_types": used_types_resolved,
        "called_methods": called_methods,
        "role_labels": roles["role_labels"],
        "roles": roles,
        "type_resolution_conflicts": uniq_conflicts(type_resolution_conflicts),
        "embedding_text": embedding_text,
        "summary": (
            f"{type_kind} {name}; properties={len(property_names)}; methods={len(method_signatures)}; "
            f"constructors={len(constructor_signatures)}; fields={len(fields)}; "
            f"roles={','.join(roles['role_labels']) or 'none'}; "
            f"skipped_methods={skipped_method_count}; "
            f"documentation={'yes' if documentation_summary else 'no'}"
        ),
    }

    if ctx.relation_mode != "compact":
        relation_records.insert(0, make_relation(
            file=ctx.rel_path,
            source_id=file_id,
            source_kind="cs_file",
            source_name=ctx.rel_path,
            relation="contains_type",
            target=name,
            target_resolved=type_id,
        ))
        relation_records.insert(1, make_relation(
            file=ctx.rel_path,
            source_id=type_id,
            source_kind="cs_type",
            source_name=name,
            relation="child_of_file",
            target=ctx.rel_path,
            target_resolved=file_id,
        ))

    return type_record, detail_records, relation_records, {
        "field_count": len(fields),
        "property_count": len(property_names),
        "method_count": len(method_signatures),
        "constructor_count": len(constructor_signatures),
        "skipped_method_count": skipped_method_count,
    }
