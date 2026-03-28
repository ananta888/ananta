from __future__ import annotations

from dataclasses import dataclass

from rag_helper.domain.java_records import (
    JavaConstructorDetailRecord,
    JavaConstructorRecord,
    JavaMethodDetailRecord,
    JavaMethodRecord,
    RelationRecord,
)
from rag_helper.extractors.java_ast_helpers import (
    extract_annotations,
    extract_identifier,
    extract_method_calls,
    extract_modifiers,
    extract_parameters,
    extract_return_type,
    extract_type_refs,
    first_child_of_type,
    make_relation,
    node_text,
)
from rag_helper.extractors.java_type_resolution import resolve_type_name, uniq_keep_order
from rag_helper.utils.ids import safe_id
from rag_helper.utils.text_normalization import compact_code_snippet


def looks_like_getter(method_name: str, params: list[str], return_type: str | None) -> bool:
    return method_name.startswith("get") and len(params) == 0 and return_type not in (None, "void")


def looks_like_boolean_getter(method_name: str, params: list[str], return_type: str | None) -> bool:
    return method_name.startswith("is") and len(params) == 0 and return_type in ("boolean", "Boolean")


def looks_like_setter(method_name: str, params: list[str], return_type: str | None) -> bool:
    return method_name.startswith("set") and len(params) == 1 and (return_type is None or return_type == "void")


@dataclass(frozen=True)
class JavaMemberContext:
    rel_path: str
    src: bytes
    package_name: str | None
    import_map: dict[str, str]
    known_package_types: dict[str, set[str]]
    same_file_types: set[str]
    include_code_snippets: bool


def extract_method(
    ctx: JavaMemberContext,
    class_name: str,
    node,
) -> tuple[JavaMethodRecord, JavaMethodDetailRecord, list[RelationRecord], dict[str, bool]]:
    name = extract_identifier(node, ctx.src) or "<method>"
    modifiers = extract_modifiers(node, ctx.src)
    annotations = extract_annotations(node, ctx.src)
    params = extract_parameters(node, ctx.src)
    return_type = extract_return_type(node, ctx.src)
    body = first_child_of_type(node, "block")

    signature = f"{name}({', '.join(params)})"
    if return_type:
        signature += f": {return_type}"

    calls = extract_method_calls(body, ctx.src)
    type_refs = extract_type_refs(node, ctx.src)
    resolved_type_refs: list[str] = []
    for tr in type_refs:
        resolved_type_refs.extend(resolve_type_name(
            tr, ctx.package_name, ctx.import_map, ctx.known_package_types, ctx.same_file_types
        ))
    resolved_type_refs = uniq_keep_order(resolved_type_refs, limit=60)

    is_getter = looks_like_getter(name, params, return_type)
    is_bool_getter = looks_like_boolean_getter(name, params, return_type)
    is_setter = looks_like_setter(name, params, return_type)
    is_trivial = is_getter or is_bool_getter or is_setter

    method_id = f"java_method:{safe_id(ctx.rel_path, class_name, signature)}"
    resolved_return_types = resolve_type_name(
        return_type, ctx.package_name, ctx.import_map, ctx.known_package_types, ctx.same_file_types
    )
    embedding_text = (
        f"Java method {name} in class {class_name}. "
        f"Signature {signature}. "
        f"Modifiers: {', '.join(modifiers) or 'none'}. "
        f"Annotations: {', '.join(annotations) or 'none'}. "
        f"Calls: {', '.join(calls[:20]) or 'none'}. "
        f"Uses resolved types: {', '.join(resolved_type_refs[:20]) or 'none'}. "
        f"Trivial accessor: {'yes' if is_trivial else 'no'}."
    )

    idx: JavaMethodRecord = {
        "kind": "java_method",
        "file": ctx.rel_path,
        "id": method_id,
        "class": class_name,
        "name": name,
        "signature": signature,
        "return_type": return_type,
        "resolved_return_types": resolved_return_types,
        "parameters": params,
        "modifiers": modifiers,
        "annotations": annotations,
        "parameter_count": len(params),
        "calls": calls[:30],
        "type_refs": type_refs[:30],
        "resolved_type_refs": resolved_type_refs,
        "is_getter": is_getter or is_bool_getter,
        "is_setter": is_setter,
        "is_trivial": is_trivial,
        "embedding_text": embedding_text,
    }

    detail: JavaMethodDetailRecord = {
        "kind": "java_method_detail",
        "file": ctx.rel_path,
        "id": f"java_method_detail:{safe_id(ctx.rel_path, class_name, signature)}",
        "class": class_name,
        "name": name,
        "signature": signature,
        "return_type": return_type,
        "resolved_return_types": resolved_return_types,
        "parameters": params,
        "modifiers": modifiers,
        "annotations": annotations,
        "calls": calls,
        "type_refs": type_refs,
        "resolved_type_refs": resolved_type_refs,
        "is_getter": is_getter or is_bool_getter,
        "is_setter": is_setter,
        "is_trivial": is_trivial,
        "embedding_text": embedding_text,
    }
    if ctx.include_code_snippets:
        detail["code_snippet"] = compact_code_snippet(node_text(ctx.src, node), max_len=3200)

    relations: list[RelationRecord] = []
    for raw_t in type_refs:
        for rt in resolve_type_name(raw_t, ctx.package_name, ctx.import_map, ctx.known_package_types, ctx.same_file_types):
            relations.append(make_relation(
                file=ctx.rel_path,
                source_id=method_id,
                source_kind="java_method",
                source_name=f"{class_name}.{name}",
                relation="uses_type",
                target=raw_t,
                target_resolved=rt,
            ))

    if return_type:
        for rt in resolved_return_types or [return_type]:
            relations.append(make_relation(
                file=ctx.rel_path,
                source_id=method_id,
                source_kind="java_method",
                source_name=f"{class_name}.{name}",
                relation="returns",
                target=return_type,
                target_resolved=rt,
            ))

    for call in calls:
        relations.append(make_relation(
            file=ctx.rel_path,
            source_id=method_id,
            source_kind="java_method",
            source_name=f"{class_name}.{name}",
            relation="calls",
            target=call,
            target_resolved=None,
        ))

    return idx, detail, relations, {"is_trivial": is_trivial}


def extract_constructor(
    ctx: JavaMemberContext,
    class_name: str,
    node,
) -> tuple[JavaConstructorRecord, JavaConstructorDetailRecord, list[RelationRecord]]:
    name = extract_identifier(node, ctx.src) or class_name
    modifiers = extract_modifiers(node, ctx.src)
    annotations = extract_annotations(node, ctx.src)
    params = extract_parameters(node, ctx.src)
    body = first_child_of_type(node, "constructor_body")

    signature = f"{name}({', '.join(params)})"
    calls = extract_method_calls(body, ctx.src)
    type_refs = extract_type_refs(node, ctx.src)
    resolved_type_refs: list[str] = []
    for tr in type_refs:
        resolved_type_refs.extend(resolve_type_name(
            tr, ctx.package_name, ctx.import_map, ctx.known_package_types, ctx.same_file_types
        ))
    resolved_type_refs = uniq_keep_order(resolved_type_refs, limit=60)

    ctor_id = f"java_constructor:{safe_id(ctx.rel_path, class_name, signature)}"
    embedding_text = (
        f"Java constructor {name} in class {class_name}. "
        f"Signature {signature}. "
        f"Modifiers: {', '.join(modifiers) or 'none'}. "
        f"Calls: {', '.join(calls[:20]) or 'none'}. "
        f"Uses resolved types: {', '.join(resolved_type_refs[:20]) or 'none'}."
    )

    idx: JavaConstructorRecord = {
        "kind": "java_constructor",
        "file": ctx.rel_path,
        "id": ctor_id,
        "class": class_name,
        "name": name,
        "signature": signature,
        "parameters": params,
        "modifiers": modifiers,
        "annotations": annotations,
        "parameter_count": len(params),
        "calls": calls[:30],
        "type_refs": type_refs[:30],
        "resolved_type_refs": resolved_type_refs,
        "embedding_text": embedding_text,
    }

    detail: JavaConstructorDetailRecord = {
        "kind": "java_constructor_detail",
        "file": ctx.rel_path,
        "id": f"java_constructor_detail:{safe_id(ctx.rel_path, class_name, signature)}",
        "class": class_name,
        "name": name,
        "signature": signature,
        "parameters": params,
        "modifiers": modifiers,
        "annotations": annotations,
        "calls": calls,
        "type_refs": type_refs,
        "resolved_type_refs": resolved_type_refs,
        "embedding_text": embedding_text,
    }
    if ctx.include_code_snippets:
        detail["code_snippet"] = compact_code_snippet(node_text(ctx.src, node), max_len=3200)

    relations: list[RelationRecord] = []
    for raw_t in type_refs:
        for rt in resolve_type_name(raw_t, ctx.package_name, ctx.import_map, ctx.known_package_types, ctx.same_file_types):
            relations.append(make_relation(
                file=ctx.rel_path,
                source_id=ctor_id,
                source_kind="java_constructor",
                source_name=f"{class_name}.{name}",
                relation="uses_type",
                target=raw_t,
                target_resolved=rt,
            ))
    for call in calls:
        relations.append(make_relation(
            file=ctx.rel_path,
            source_id=ctor_id,
            source_kind="java_constructor",
            source_name=f"{class_name}.{name}",
            relation="calls",
            target=call,
            target_resolved=None,
        ))

    return idx, detail, relations
