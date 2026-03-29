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
from rag_helper.extractors.java_type_resolution import (
    find_resolution_conflicts,
    resolve_type_name,
    split_generics,
    uniq_conflicts,
    uniq_keep_order,
)
from rag_helper.utils.embedding_text import build_embedding_text, compact_list
from rag_helper.utils.ids import safe_id


@dataclass(frozen=True)
class JavaTypeContext:
    rel_path: str
    src: bytes
    package_name: str | None
    imports: list[str]
    import_map: dict[str, str]
    wildcard_imports: list[str]
    known_package_types: dict[str, set[str]]
    same_file_types: set[str]
    include_code_snippets: bool
    exclude_trivial_methods: bool
    max_methods_per_class: int | None
    relation_mode: str
    mark_import_conflicts: bool
    resolve_method_targets: bool
    resolve_framework_relations: bool
    embedding_text_mode: str = "verbose"


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
    skipped_method_count = 0
    type_resolution_conflicts: list[dict[str, object]] = []

    type_id = f"java_type:{safe_id(ctx.rel_path, name, type_kind)}"
    file_id = f"java_file:{safe_id(ctx.rel_path)}"
    if body:
        field_type_lookup: dict[str, list[str]] = {}
        for member in body.children:
            if member.type != "field_declaration":
                continue
            field = extract_field(member, ctx.src)
            resolved_field_types = resolve_type_name(
                field.get("type"),
                package_name=ctx.package_name,
                import_map=ctx.import_map,
                known_package_types=ctx.known_package_types,
                same_file_types=ctx.same_file_types,
                wildcard_imports=ctx.wildcard_imports,
            )
            for declarator in field.get("declarators", []):
                variable_name = declarator.split("=")[0].strip()
                if variable_name:
                    field_type_lookup[variable_name] = resolved_field_types

        member_ctx = JavaMemberContext(
            rel_path=ctx.rel_path,
            src=ctx.src,
            package_name=ctx.package_name,
            import_map=ctx.import_map,
            wildcard_imports=ctx.wildcard_imports,
            known_package_types=ctx.known_package_types,
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
                field = extract_field(member, ctx.src)
                field["resolved_types"] = resolve_type_name(
                    field.get("type"),
                    package_name=ctx.package_name,
                    import_map=ctx.import_map,
                    known_package_types=ctx.known_package_types,
                    same_file_types=ctx.same_file_types,
                    wildcard_imports=ctx.wildcard_imports,
                )
                if ctx.mark_import_conflicts:
                    type_resolution_conflicts.extend(
                        find_resolution_conflicts(field.get("type") or "", field["resolved_types"])
                    )
                fields.append(field)
                field_type_resolved.extend(field["resolved_types"])

                for raw_t in split_generics(field.get("type") or ""):
                    for rt in resolve_type_name(
                        raw_t,
                        ctx.package_name,
                        ctx.import_map,
                        ctx.known_package_types,
                        ctx.same_file_types,
                        wildcard_imports=ctx.wildcard_imports,
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
                if ctx.resolve_framework_relations:
                    relation_records.extend(build_field_framework_relations(
                        rel_path=ctx.rel_path,
                        type_id=type_id,
                        type_name=name,
                        field=field,
                    ))

            elif member.type == "method_declaration":
                m_index, m_detail, m_relations, m_meta = extract_method(member_ctx, name, type_id, member)
                if ctx.exclude_trivial_methods and m_meta["is_trivial"]:
                    continue
                if (
                    ctx.max_methods_per_class is not None
                    and len(method_signatures) >= ctx.max_methods_per_class
                ):
                    skipped_method_count += 1
                    continue
                method_signatures.append(m_index["signature"])
                called_methods.extend(m_index["calls"])
                used_types_resolved.extend(m_index["resolved_type_refs"])
                method_indexes.append(m_index)
                detail_records.append(m_index)
                detail_records.append(m_detail)
                relation_records.extend(m_relations)
                if ctx.mark_import_conflicts:
                    type_resolution_conflicts.extend(m_index.get("type_resolution_conflicts", []))
                if ctx.relation_mode != "compact":
                    relation_records.append(make_relation(
                        file=ctx.rel_path,
                        source_id=type_id,
                        source_kind="java_type",
                        source_name=name,
                        relation="declares_method",
                        target=m_index["name"],
                        target_resolved=m_index["id"],
                    ))
                    relation_records.append(make_relation(
                        file=ctx.rel_path,
                        source_id=m_index["id"],
                        source_kind="java_method",
                        source_name=f"{name}.{m_index['name']}",
                        relation="child_of_type",
                        target=name,
                        target_resolved=type_id,
                    ))
                if ctx.resolve_framework_relations:
                    relation_records.extend(build_method_framework_relations(
                        rel_path=ctx.rel_path,
                        type_id=type_id,
                        type_name=name,
                        method_record=m_index,
                    ))

            elif member.type == "constructor_declaration":
                c_index, c_detail, c_relations = extract_constructor(member_ctx, name, type_id, member)
                constructor_signatures.append(c_index["signature"])
                called_methods.extend(c_index["calls"])
                used_types_resolved.extend(c_index["resolved_type_refs"])
                detail_records.append(c_index)
                detail_records.append(c_detail)
                relation_records.extend(c_relations)
                if ctx.mark_import_conflicts:
                    type_resolution_conflicts.extend(c_index.get("type_resolution_conflicts", []))
                if ctx.relation_mode != "compact":
                    relation_records.append(make_relation(
                        file=ctx.rel_path,
                        source_id=type_id,
                        source_kind="java_type",
                        source_name=name,
                        relation="declares_constructor",
                        target=c_index["signature"],
                        target_resolved=c_index["id"],
                    ))
                    relation_records.append(make_relation(
                        file=ctx.rel_path,
                        source_id=c_index["id"],
                        source_kind="java_constructor",
                        source_name=f"{name}.{c_index['name']}",
                        relation="child_of_type",
                        target=name,
                        target_resolved=type_id,
                    ))
                if ctx.resolve_framework_relations:
                    relation_records.extend(build_constructor_framework_relations(
                        rel_path=ctx.rel_path,
                        type_id=type_id,
                        type_name=name,
                        constructor_record=c_index,
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
        extends,
        ctx.package_name,
        ctx.import_map,
        ctx.known_package_types,
        ctx.same_file_types,
        wildcard_imports=ctx.wildcard_imports,
    )
    if ctx.mark_import_conflicts:
        type_resolution_conflicts.extend(find_resolution_conflicts(extends or "", extends_resolved))
    implements_resolved = []
    for impl in implements:
        resolved_impl = resolve_type_name(
            impl,
            ctx.package_name,
            ctx.import_map,
            ctx.known_package_types,
            ctx.same_file_types,
            wildcard_imports=ctx.wildcard_imports,
        )
        implements_resolved.extend(resolved_impl)
        if ctx.mark_import_conflicts:
            type_resolution_conflicts.extend(find_resolution_conflicts(impl, resolved_impl))

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
    if ctx.resolve_framework_relations:
        relation_records.extend(build_type_framework_relations(
            rel_path=ctx.rel_path,
            type_id=type_id,
            type_name=name,
            annotations=annotations,
            resolved_extends=extends_resolved,
        ))

    for impl in implements:
        impl_resolved = resolve_type_name(
            impl,
            ctx.package_name,
            ctx.import_map,
            ctx.known_package_types,
            ctx.same_file_types,
            wildcard_imports=ctx.wildcard_imports,
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

    embedding_text = build_embedding_text(
        ctx.embedding_text_mode,
        (
            f"Java {type_kind} {name} in file {ctx.rel_path}. "
            f"Package {ctx.package_name or 'default'}. "
            f"Roles: {', '.join(roles['role_labels']) or 'none'}. "
            f"Modifiers: {', '.join(modifiers) or 'none'}. "
            f"Annotations: {', '.join(annotations) or 'none'}. "
            f"Extends {extends or 'none'}. Implements {', '.join(implements) or 'none'}. "
            f"Methods: {', '.join(method_signatures[:20]) or 'none'}. "
            f"Used types: {', '.join(used_types_resolved[:20]) or 'none'}. "
            f"Calls: {', '.join(called_methods[:20]) or 'none'}."
        ),
        (
            f"Java {type_kind} {name}. Package {ctx.package_name or 'default'}. "
            f"Roles {compact_list(roles['role_labels'], limit=4)}. "
            f"Methods {compact_list(method_signatures, limit=6)}. "
            f"Used types {compact_list(used_types_resolved, limit=6)}."
        ),
    )

    type_record: JavaTypeRecord = {
        "kind": "java_type",
        "file": ctx.rel_path,
        "id": type_id,
        "parent_id": file_id,
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
        "type_resolution_conflicts": uniq_conflicts(type_resolution_conflicts),
        "embedding_text": embedding_text,
        "summary": (
            f"{type_kind} {name}; methods={len(method_signatures)}; "
            f"constructors={len(constructor_signatures)}; fields={len(fields)}; "
            f"roles={','.join(roles['role_labels']) or 'none'}; "
            f"skipped_methods={skipped_method_count}"
        ),
    }

    if ctx.relation_mode != "compact":
        relation_records.insert(0, make_relation(
            file=ctx.rel_path,
            source_id=file_id,
            source_kind="java_file",
            source_name=ctx.rel_path,
            relation="contains_type",
            target=name,
            target_resolved=type_id,
        ))
        relation_records.insert(1, make_relation(
            file=ctx.rel_path,
            source_id=type_id,
            source_kind="java_type",
            source_name=name,
            relation="child_of_file",
            target=ctx.rel_path,
            target_resolved=file_id,
        ))

    return type_record, detail_records, relation_records, {
        "field_count": len(fields),
        "method_count": len(method_signatures),
        "constructor_count": len(constructor_signatures),
        "skipped_method_count": skipped_method_count,
    }


def has_annotation_prefix(annotations: list[str], prefixes: tuple[str, ...]) -> bool:
    return any(annotation.startswith(prefix) for annotation in annotations for prefix in prefixes)


def build_type_framework_relations(
    rel_path: str,
    type_id: str,
    type_name: str,
    annotations: list[str],
    resolved_extends: list[str],
) -> list[RelationRecord]:
    relations: list[RelationRecord] = []
    if has_annotation_prefix(annotations, ("@Configuration",)):
        relations.append(make_relation(
            file=rel_path,
            source_id=type_id,
            source_kind="java_type",
            source_name=type_name,
            relation="spring_configuration",
            target=type_name,
            target_resolved=type_id,
        ))
    if has_annotation_prefix(annotations, ("@Entity", "@Embeddable", "@MappedSuperclass")):
        relations.append(make_relation(
            file=rel_path,
            source_id=type_id,
            source_kind="java_type",
            source_name=type_name,
            relation="jpa_entity_role",
            target=type_name,
            target_resolved=type_id,
        ))
    if has_annotation_prefix(annotations, ("@Transactional",)):
        relations.append(make_relation(
            file=rel_path,
            source_id=type_id,
            source_kind="java_type",
            source_name=type_name,
            relation="transactional_boundary",
            target=type_name,
            target_resolved=type_id,
        ))
    for resolved_base in resolved_extends:
        if resolved_base.endswith("JpaRepository") or resolved_base.endswith("CrudRepository"):
            relations.append(make_relation(
                file=rel_path,
                source_id=type_id,
                source_kind="java_type",
                source_name=type_name,
                relation="repository_extends_framework",
                target=resolved_base,
                target_resolved=resolved_base,
            ))
    return relations


def build_field_framework_relations(
    rel_path: str,
    type_id: str,
    type_name: str,
    field: dict[str, Any],
) -> list[RelationRecord]:
    relations: list[RelationRecord] = []
    annotations = field.get("annotations", [])
    declarators = field.get("declarators", [])
    resolved_types = field.get("resolved_types", [])
    primary_target = resolved_types[0] if resolved_types else field.get("type")

    if has_annotation_prefix(annotations, ("@Autowired", "@Inject")) and primary_target:
        for declarator in declarators:
            field_name = declarator.split("=")[0].strip()
            relations.append(make_relation(
                file=rel_path,
                source_id=type_id,
                source_kind="java_type",
                source_name=type_name,
                relation="injects_dependency",
                target=field_name or (field.get("type") or ""),
                target_resolved=primary_target,
            ))

    jpa_map = {
        "@OneToMany": "jpa_one_to_many",
        "@ManyToOne": "jpa_many_to_one",
        "@OneToOne": "jpa_one_to_one",
        "@ManyToMany": "jpa_many_to_many",
    }
    for annotation_prefix, relation_name in jpa_map.items():
        if has_annotation_prefix(annotations, (annotation_prefix,)) and primary_target:
            relations.append(make_relation(
                file=rel_path,
                source_id=type_id,
                source_kind="java_type",
                source_name=type_name,
                relation=relation_name,
                target=field.get("type") or "",
                target_resolved=primary_target,
            ))

    if has_annotation_prefix(annotations, ("@JoinColumn",)):
        relations.append(make_relation(
            file=rel_path,
            source_id=type_id,
            source_kind="java_type",
            source_name=type_name,
            relation="jpa_join_column",
            target=field.get("type") or "",
            target_resolved=primary_target,
        ))

    return relations


def build_method_framework_relations(
    rel_path: str,
    type_id: str,
    type_name: str,
    method_record: dict[str, Any],
) -> list[RelationRecord]:
    relations: list[RelationRecord] = []
    annotations = method_record.get("annotations", [])
    method_id = method_record["id"]
    method_name = method_record["name"]

    if has_annotation_prefix(annotations, ("@Bean",)):
        target_resolved = method_record.get("resolved_return_types", [None])[0] or method_record.get("return_type")
        relations.append(make_relation(
            file=rel_path,
            source_id=type_id,
            source_kind="java_type",
            source_name=type_name,
            relation="declares_bean",
            target=method_name,
            target_resolved=target_resolved,
        ))
        relations.append(make_relation(
            file=rel_path,
            source_id=method_id,
            source_kind="java_method",
            source_name=f"{type_name}.{method_name}",
            relation="bean_factory_method",
            target=method_name,
            target_resolved=target_resolved,
        ))

    if has_annotation_prefix(annotations, ("@Transactional",)):
        relations.append(make_relation(
            file=rel_path,
            source_id=method_id,
            source_kind="java_method",
            source_name=f"{type_name}.{method_name}",
            relation="transactional_boundary",
            target=method_name,
            target_resolved=method_id,
        ))

    return relations


def build_constructor_framework_relations(
    rel_path: str,
    type_id: str,
    type_name: str,
    constructor_record: dict[str, Any],
) -> list[RelationRecord]:
    relations: list[RelationRecord] = []
    annotations = constructor_record.get("annotations", [])
    if has_annotation_prefix(annotations, ("@Autowired", "@Inject")):
        relations.append(make_relation(
            file=rel_path,
            source_id=type_id,
            source_kind="java_type",
            source_name=type_name,
            relation="constructor_injection",
            target=constructor_record["signature"],
            target_resolved=constructor_record["id"],
        ))
    return relations
