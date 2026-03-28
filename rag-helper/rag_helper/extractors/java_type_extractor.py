from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rag_helper.domain.java_records import JavaTypeRecord, RelationRecord
from rag_helper.extractors.java_ast_helpers import (
    extract_annotations,
    extract_extends_implements,
    extract_field,
    extract_identifier,
    extract_modifiers,
    first_child_of_type,
    make_relation,
)
from rag_helper.extractors.java_member_extractor import JavaMemberContext, extract_constructor, extract_method
from rag_helper.extractors.java_role_detection import detect_type_roles
from rag_helper.extractors.java_type_resolution import resolve_type_name, split_generics, uniq_keep_order
from rag_helper.utils.ids import safe_id


@dataclass(frozen=True)
class JavaTypeContext:
    rel_path: str
    src: bytes
    package_name: str | None
    imports: list[str]
    import_map: dict[str, str]
    known_package_types: dict[str, set[str]]
    same_file_types: set[str]
    include_code_snippets: bool
    exclude_trivial_methods: bool


def extract_type(
    ctx: JavaTypeContext,
    node,
) -> tuple[JavaTypeRecord, list[dict[str, Any]], list[RelationRecord], dict[str, int]]:
    name = extract_identifier(node, ctx.src) or "<anonymous>"
    type_kind = node.type.replace("_declaration", "")
    modifiers = extract_modifiers(node, ctx.src)
    annotations = extract_annotations(node, ctx.src)
    extends, implements = extract_extends_implements(node, ctx.src)

    body = first_child_of_type(node, "class_body")
    if body is None and type_kind == "record":
        for c in node.children:
            if c.type == "class_body":
                body = c
                break

    fields: list[dict[str, Any]] = []
    method_signatures: list[str] = []
    constructor_signatures: list[str] = []
    detail_records: list[dict[str, Any]] = []
    relation_records: list[RelationRecord] = []
    method_indexes: list[dict[str, Any]] = []
    field_type_resolved: list[str] = []
    called_methods: list[str] = []
    used_types_resolved: list[str] = []

    type_id = f"java_type:{safe_id(ctx.rel_path, name, type_kind)}"
    member_ctx = JavaMemberContext(
        rel_path=ctx.rel_path,
        src=ctx.src,
        package_name=ctx.package_name,
        import_map=ctx.import_map,
        known_package_types=ctx.known_package_types,
        same_file_types=ctx.same_file_types,
        include_code_snippets=ctx.include_code_snippets,
    )

    if body:
        for member in body.children:
            if member.type == "field_declaration":
                field = extract_field(member, ctx.src)
                field["resolved_types"] = resolve_type_name(
                    field.get("type"),
                    package_name=ctx.package_name,
                    import_map=ctx.import_map,
                    known_package_types=ctx.known_package_types,
                    same_file_types=ctx.same_file_types,
                )
                fields.append(field)
                field_type_resolved.extend(field["resolved_types"])

                for raw_t in split_generics(field.get("type") or ""):
                    for rt in resolve_type_name(
                        raw_t, ctx.package_name, ctx.import_map, ctx.known_package_types, ctx.same_file_types
                    ):
                        relation_records.append(make_relation(
                            file=ctx.rel_path,
                            source_id=type_id,
                            source_kind="java_type",
                            source_name=name,
                            relation="field_type_uses",
                            target=raw_t,
                            target_resolved=rt,
                        ))

            elif member.type == "method_declaration":
                m_index, m_detail, m_relations, m_meta = extract_method(member_ctx, name, member)
                if ctx.exclude_trivial_methods and m_meta["is_trivial"]:
                    continue
                method_signatures.append(m_index["signature"])
                called_methods.extend(m_index["calls"])
                used_types_resolved.extend(m_index["resolved_type_refs"])
                method_indexes.append(m_index)
                detail_records.append(m_index)
                detail_records.append(m_detail)
                relation_records.extend(m_relations)
                relation_records.append(make_relation(
                    file=ctx.rel_path,
                    source_id=type_id,
                    source_kind="java_type",
                    source_name=name,
                    relation="declares_method",
                    target=m_index["name"],
                    target_resolved=m_index["id"],
                ))

            elif member.type == "constructor_declaration":
                c_index, c_detail, c_relations = extract_constructor(member_ctx, name, member)
                constructor_signatures.append(c_index["signature"])
                called_methods.extend(c_index["calls"])
                used_types_resolved.extend(c_index["resolved_type_refs"])
                detail_records.append(c_index)
                detail_records.append(c_detail)
                relation_records.extend(c_relations)
                relation_records.append(make_relation(
                    file=ctx.rel_path,
                    source_id=type_id,
                    source_kind="java_type",
                    source_name=name,
                    relation="declares_constructor",
                    target=c_index["signature"],
                    target_resolved=c_index["id"],
                ))

    roles = detect_type_roles(
        type_name=name,
        type_kind=type_kind,
        annotations=annotations,
        imports=ctx.imports,
        fields=fields,
        methods=method_indexes,
    )

    extends_resolved = resolve_type_name(
        extends, ctx.package_name, ctx.import_map, ctx.known_package_types, ctx.same_file_types
    )
    implements_resolved = []
    for impl in implements:
        implements_resolved.extend(resolve_type_name(
            impl, ctx.package_name, ctx.import_map, ctx.known_package_types, ctx.same_file_types
        ))

    used_types_resolved = uniq_keep_order(
        used_types_resolved + field_type_resolved + extends_resolved + implements_resolved, limit=80
    )
    called_methods = uniq_keep_order(called_methods, limit=50)

    if extends:
        for rt in extends_resolved or [extends]:
            relation_records.append(make_relation(
                file=ctx.rel_path,
                source_id=type_id,
                source_kind="java_type",
                source_name=name,
                relation="extends",
                target=extends,
                target_resolved=rt,
            ))

    for impl in implements:
        impl_resolved = resolve_type_name(
            impl, ctx.package_name, ctx.import_map, ctx.known_package_types, ctx.same_file_types
        )
        for rt in impl_resolved or [impl]:
            relation_records.append(make_relation(
                file=ctx.rel_path,
                source_id=type_id,
                source_kind="java_type",
                source_name=name,
                relation="implements",
                target=impl,
                target_resolved=rt,
            ))

    embedding_text = (
        f"Java {type_kind} {name} in file {ctx.rel_path}. "
        f"Package {ctx.package_name or 'default'}. "
        f"Roles: {', '.join(roles['role_labels']) or 'none'}. "
        f"Modifiers: {', '.join(modifiers) or 'none'}. "
        f"Annotations: {', '.join(annotations) or 'none'}. "
        f"Extends {extends or 'none'}. Implements {', '.join(implements) or 'none'}. "
        f"Methods: {', '.join(method_signatures[:20]) or 'none'}. "
        f"Used types: {', '.join(used_types_resolved[:20]) or 'none'}. "
        f"Calls: {', '.join(called_methods[:20]) or 'none'}."
    )

    type_record: JavaTypeRecord = {
        "kind": "java_type",
        "file": ctx.rel_path,
        "id": type_id,
        "package": ctx.package_name,
        "imports": ctx.imports,
        "name": name,
        "type_kind": type_kind,
        "modifiers": modifiers,
        "annotations": annotations,
        "extends": extends,
        "extends_resolved": extends_resolved,
        "implements": implements,
        "implements_resolved": implements_resolved,
        "fields": fields[:50],
        "methods": method_signatures[:200],
        "constructors": constructor_signatures[:50],
        "used_types": used_types_resolved,
        "called_methods": called_methods,
        "role_labels": roles["role_labels"],
        "roles": roles,
        "embedding_text": embedding_text,
        "summary": (
            f"{type_kind} {name}; methods={len(method_signatures)}; "
            f"constructors={len(constructor_signatures)}; fields={len(fields)}; "
            f"roles={','.join(roles['role_labels']) or 'none'}"
        ),
    }

    return type_record, detail_records, relation_records, {
        "field_count": len(fields),
        "method_count": len(method_signatures),
        "constructor_count": len(constructor_signatures),
    }
