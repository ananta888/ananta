from __future__ import annotations

from dataclasses import dataclass
import re

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
    extract_javadoc,
    extract_javadoc_summary,
    extract_method_calls,
    extract_modifiers,
    extract_parameters,
    extract_return_type,
    extract_type_refs,
    first_child_of_type,
    make_relation,
    node_text,
)
from rag_helper.extractors.java_type_resolution import (
    find_resolution_conflicts,
    resolve_type_name,
    uniq_conflicts,
    uniq_keep_order,
)
from rag_helper.utils.embedding_text import build_embedding_text, compact_list
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
    wildcard_imports: list[str]
    known_package_types: dict[str, set[str]]
    same_file_types: set[str]
    include_code_snippets: bool
    relation_mode: str
    embedding_text_mode: str
    mark_import_conflicts: bool
    resolve_method_targets: bool
    field_type_lookup: dict[str, list[str]]


def extract_method(
    ctx: JavaMemberContext,
    class_name: str,
    parent_type_id: str,
    node,
) -> tuple[JavaMethodRecord, JavaMethodDetailRecord, list[RelationRecord], dict[str, bool]]:
    name = extract_identifier(node, ctx.src) or "<method>"
    modifiers = extract_modifiers(node, ctx.src)
    annotations = extract_annotations(node, ctx.src)
    javadoc = extract_javadoc(node, ctx.src)
    javadoc_summary = extract_javadoc_summary(javadoc)
    params = extract_parameters(node, ctx.src)
    return_type = extract_return_type(node, ctx.src)
    body = first_child_of_type(node, "block")

    signature = f"{name}({', '.join(params)})"
    if return_type:
        signature += f": {return_type}"

    calls = extract_method_calls(body, ctx.src)
    type_refs = extract_type_refs(node, ctx.src)
    resolved_type_refs: list[str] = []
    resolution_conflicts: list[dict[str, object]] = []
    for tr in type_refs:
        resolved = resolve_type_name(
            tr,
            ctx.package_name,
            ctx.import_map,
            ctx.known_package_types,
            ctx.same_file_types,
            wildcard_imports=ctx.wildcard_imports,
        )
        resolved_type_refs.extend(resolved)
        if ctx.mark_import_conflicts:
            resolution_conflicts.extend(find_resolution_conflicts(tr, resolved))
    resolved_type_refs = uniq_keep_order(resolved_type_refs, limit=60)

    is_getter = looks_like_getter(name, params, return_type)
    is_bool_getter = looks_like_boolean_getter(name, params, return_type)
    is_setter = looks_like_setter(name, params, return_type)
    is_trivial = is_getter or is_bool_getter or is_setter

    method_id = f"java_method:{safe_id(ctx.rel_path, class_name, signature)}"
    resolved_return_types = resolve_type_name(
        return_type,
        ctx.package_name,
        ctx.import_map,
        ctx.known_package_types,
        ctx.same_file_types,
        wildcard_imports=ctx.wildcard_imports,
    )
    if ctx.mark_import_conflicts:
        resolution_conflicts.extend(find_resolution_conflicts(return_type or "", resolved_return_types))
    resolution_conflicts = uniq_conflicts(resolution_conflicts)
    resolved_call_targets = resolve_call_targets(
        calls=calls,
        class_name=class_name,
        package_name=ctx.package_name,
        field_type_lookup=ctx.field_type_lookup,
        parameter_bindings=parse_parameter_bindings(params),
        same_file_types=ctx.same_file_types,
        resolve_enabled=ctx.resolve_method_targets,
    )
    embedding_text = build_embedding_text(
        ctx.embedding_text_mode,
        (
            f"Java method {name} in class {class_name}. "
            f"Signature {signature}. "
            f"Modifiers: {', '.join(modifiers) or 'none'}. "
            f"Annotations: {', '.join(annotations) or 'none'}. "
            f"Javadoc: {javadoc_summary or 'none'}. "
            f"Calls: {', '.join(calls[:20]) or 'none'}. "
            f"Uses resolved types: {', '.join(resolved_type_refs[:20]) or 'none'}. "
            f"Trivial accessor: {'yes' if is_trivial else 'no'}."
        ),
        (
            f"Java method {class_name}.{name}. "
            f"Signature {signature}. "
            f"Doc {javadoc_summary or 'none'}. "
            f"Calls {compact_list(calls, limit=6)}. "
            f"Types {compact_list(resolved_type_refs, limit=6)}."
        ),
    )

    idx: JavaMethodRecord = {
        "kind": "java_method",
        "file": ctx.rel_path,
        "id": method_id,
        "parent_id": parent_type_id,
        "class": class_name,
        "name": name,
        "signature": signature,
        "return_type": return_type,
        "resolved_return_types": resolved_return_types,
        "parameters": params,
        "modifiers": modifiers,
        "annotations": annotations,
        "javadoc": javadoc,
        "javadoc_summary": javadoc_summary,
        "parameter_count": len(params),
        "calls": calls[:30],
        "type_refs": type_refs[:30],
        "resolved_type_refs": resolved_type_refs,
        "is_getter": is_getter or is_bool_getter,
        "is_setter": is_setter,
        "is_trivial": is_trivial,
        "type_resolution_conflicts": resolution_conflicts,
        "resolved_call_targets": resolved_call_targets,
        "embedding_text": embedding_text,
    }

    detail: JavaMethodDetailRecord = {
        "kind": "java_method_detail",
        "file": ctx.rel_path,
        "id": f"java_method_detail:{safe_id(ctx.rel_path, class_name, signature)}",
        "parent_id": method_id,
        "class": class_name,
        "name": name,
        "signature": signature,
        "return_type": return_type,
        "resolved_return_types": resolved_return_types,
        "parameters": params,
        "modifiers": modifiers,
        "annotations": annotations,
        "javadoc": javadoc,
        "javadoc_summary": javadoc_summary,
        "calls": calls,
        "type_refs": type_refs,
        "resolved_type_refs": resolved_type_refs,
        "is_getter": is_getter or is_bool_getter,
        "is_setter": is_setter,
        "is_trivial": is_trivial,
        "type_resolution_conflicts": resolution_conflicts,
        "resolved_call_targets": resolved_call_targets,
        "embedding_text": embedding_text,
    }
    if ctx.include_code_snippets:
        detail["code_snippet"] = compact_code_snippet(node_text(ctx.src, node), max_len=3200)

    relations: list[RelationRecord] = []
    for raw_t in type_refs:
        resolved_targets = resolve_type_name(
            raw_t,
            ctx.package_name,
            ctx.import_map,
            ctx.known_package_types,
            ctx.same_file_types,
            wildcard_imports=ctx.wildcard_imports,
        )
        for rt in resolved_targets:
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

    if ctx.relation_mode != "compact":
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
    for call_target in resolved_call_targets:
        relations.append(make_relation(
            file=ctx.rel_path,
            source_id=method_id,
            source_kind="java_method",
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


def extract_constructor(
    ctx: JavaMemberContext,
    class_name: str,
    parent_type_id: str,
    node,
) -> tuple[JavaConstructorRecord, JavaConstructorDetailRecord, list[RelationRecord]]:
    name = extract_identifier(node, ctx.src) or class_name
    modifiers = extract_modifiers(node, ctx.src)
    annotations = extract_annotations(node, ctx.src)
    javadoc = extract_javadoc(node, ctx.src)
    javadoc_summary = extract_javadoc_summary(javadoc)
    params = extract_parameters(node, ctx.src)
    body = first_child_of_type(node, "constructor_body")

    signature = f"{name}({', '.join(params)})"
    calls = extract_method_calls(body, ctx.src)
    type_refs = extract_type_refs(node, ctx.src)
    resolved_type_refs: list[str] = []
    resolution_conflicts: list[dict[str, object]] = []
    for tr in type_refs:
        resolved = resolve_type_name(
            tr,
            ctx.package_name,
            ctx.import_map,
            ctx.known_package_types,
            ctx.same_file_types,
            wildcard_imports=ctx.wildcard_imports,
        )
        resolved_type_refs.extend(resolved)
        if ctx.mark_import_conflicts:
            resolution_conflicts.extend(find_resolution_conflicts(tr, resolved))
    resolved_type_refs = uniq_keep_order(resolved_type_refs, limit=60)
    resolution_conflicts = uniq_conflicts(resolution_conflicts)
    resolved_call_targets = resolve_call_targets(
        calls=calls,
        class_name=class_name,
        package_name=ctx.package_name,
        field_type_lookup=ctx.field_type_lookup,
        parameter_bindings=parse_parameter_bindings(params),
        same_file_types=ctx.same_file_types,
        resolve_enabled=ctx.resolve_method_targets,
    )

    ctor_id = f"java_constructor:{safe_id(ctx.rel_path, class_name, signature)}"
    embedding_text = build_embedding_text(
        ctx.embedding_text_mode,
        (
            f"Java constructor {name} in class {class_name}. "
            f"Signature {signature}. "
            f"Modifiers: {', '.join(modifiers) or 'none'}. "
            f"Javadoc: {javadoc_summary or 'none'}. "
            f"Calls: {', '.join(calls[:20]) or 'none'}. "
            f"Uses resolved types: {', '.join(resolved_type_refs[:20]) or 'none'}."
        ),
        (
            f"Java constructor {class_name}.{name}. "
            f"Signature {signature}. "
            f"Doc {javadoc_summary or 'none'}. "
            f"Calls {compact_list(calls, limit=6)}. "
            f"Types {compact_list(resolved_type_refs, limit=6)}."
        ),
    )

    idx: JavaConstructorRecord = {
        "kind": "java_constructor",
        "file": ctx.rel_path,
        "id": ctor_id,
        "parent_id": parent_type_id,
        "class": class_name,
        "name": name,
        "signature": signature,
        "parameters": params,
        "modifiers": modifiers,
        "annotations": annotations,
        "javadoc": javadoc,
        "javadoc_summary": javadoc_summary,
        "parameter_count": len(params),
        "calls": calls[:30],
        "type_refs": type_refs[:30],
        "resolved_type_refs": resolved_type_refs,
        "type_resolution_conflicts": resolution_conflicts,
        "resolved_call_targets": resolved_call_targets,
        "embedding_text": embedding_text,
    }

    detail: JavaConstructorDetailRecord = {
        "kind": "java_constructor_detail",
        "file": ctx.rel_path,
        "id": f"java_constructor_detail:{safe_id(ctx.rel_path, class_name, signature)}",
        "parent_id": ctor_id,
        "class": class_name,
        "name": name,
        "signature": signature,
        "parameters": params,
        "modifiers": modifiers,
        "annotations": annotations,
        "javadoc": javadoc,
        "javadoc_summary": javadoc_summary,
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
    for raw_t in type_refs:
        resolved_targets = resolve_type_name(
            raw_t,
            ctx.package_name,
            ctx.import_map,
            ctx.known_package_types,
            ctx.same_file_types,
            wildcard_imports=ctx.wildcard_imports,
        )
        for rt in resolved_targets:
            relations.append(make_relation(
                file=ctx.rel_path,
                source_id=ctor_id,
                source_kind="java_constructor",
                source_name=f"{class_name}.{name}",
                relation="uses_type",
                target=raw_t,
                target_resolved=rt,
            ))
    if ctx.relation_mode != "compact":
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
    for call_target in resolved_call_targets:
        relations.append(make_relation(
            file=ctx.rel_path,
            source_id=ctor_id,
            source_kind="java_constructor",
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


def parse_parameter_bindings(params: list[str]) -> dict[str, str]:
    bindings: dict[str, str] = {}
    for param in params:
        cleaned = " ".join(part for part in param.split() if not part.startswith("@"))
        match = PARAM_PATTERN.search(cleaned.replace("...", "[]"))
        if match:
            bindings[match.group("name")] = match.group("type")
    return bindings


def resolve_call_targets(
    calls: list[str],
    class_name: str,
    package_name: str | None,
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
            resolved_class = f"{package_name}.{qualifier}" if package_name and qualifier in same_file_types else qualifier
            candidates = [f"{resolved_class}.{method_name}"]
        elif not qualifier:
            resolved_class = f"{package_name}.{class_name}" if package_name else class_name
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
