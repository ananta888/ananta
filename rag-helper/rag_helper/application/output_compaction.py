from __future__ import annotations

from typing import Any


AGGRESSIVE_INDEX_KINDS = {
    "java_type",
    "xml_tag_summary",
    "adoc_section",
    "xsd_complex_type",
    "xsd_root_element",
}

AGGRESSIVE_DETAIL_KINDS = {
    "adoc_architecture_chunk",
    "adoc_section_detail",
    "jpa_entity_chunk",
    "md_section",
    "properties_entry",
    "sql_statement",
    "yaml_entry",
}

PARENT_LINKED_DETAIL_KINDS = {
    "adoc_architecture_chunk",
    "adoc_section_detail",
    "jpa_entity_chunk",
}

AGGRESSIVE_RELATION_TYPES = {
    "bean_factory_method",
    "declares_bean",
    "extends",
    "field_type_uses",
    "implements",
    "injects_dependency",
    "jpa_entity_role",
    "jpa_join_column",
    "jpa_many_to_many",
    "jpa_many_to_one",
    "jpa_one_to_many",
    "jpa_one_to_one",
    "spring_configuration",
    "transactional_boundary",
}

ULTRA_DETAIL_KINDS = {
    "adoc_architecture_chunk",
    "jpa_entity_chunk",
}

ULTRA_RELATION_TYPES = {
    "declares_bean",
    "extends",
    "implements",
    "injects_dependency",
    "jpa_entity_role",
    "jpa_join_column",
    "jpa_many_to_many",
    "jpa_many_to_one",
    "jpa_one_to_many",
    "jpa_one_to_one",
    "spring_configuration",
    "transactional_boundary",
}


def compact_output_records(
    index_records: list[dict],
    detail_records: list[dict],
    relation_records: list[dict],
    mode: str,
) -> tuple[list[dict], list[dict], list[dict]]:
    if mode not in {"aggressive", "ultra", "ultra-rich"}:
        return index_records, detail_records, relation_records

    ultra_mode = mode in {"ultra", "ultra-rich"}
    rich_java_mode = mode == "ultra-rich"
    if ultra_mode:
        preserved_xsd_index = [record for record in index_records if _is_xsd_record(record)]
        preserved_xsd_details = [record for record in detail_records if _is_xsd_record(record)]
        preserved_xsd_relations = [record for record in relation_records if _is_xsd_relation(record)]
        index_records = [record for record in index_records if not _is_xsd_record(record)]
        detail_records = [record for record in detail_records if not _is_xsd_record(record)]
        relation_records = [record for record in relation_records if not _is_xsd_relation(record)]
    else:
        preserved_xsd_index = []
        preserved_xsd_details = []
        preserved_xsd_relations = []

    compact_index = [
        _compact_index_record(record, ultra_mode=ultra_mode, rich_java_mode=rich_java_mode)
        for record in index_records
        if _keep_index_record(record, ultra_mode=ultra_mode, rich_java_mode=rich_java_mode)
    ]
    kept_ids = {record.get("id") for record in compact_index}
    compact_details = []
    allowed_detail_kinds = ULTRA_DETAIL_KINDS if ultra_mode else AGGRESSIVE_DETAIL_KINDS
    for record in detail_records:
        if record.get("kind") not in allowed_detail_kinds:
            continue
        if (
            record.get("kind") in PARENT_LINKED_DETAIL_KINDS
            and record.get("parent_id")
            and record.get("parent_id") not in kept_ids
        ):
            continue
        compact_record = _compact_detail_record(record, ultra_mode=ultra_mode)
        compact_details.append(compact_record)
        kept_ids.add(compact_record.get("id"))
    allowed_relation_types = ULTRA_RELATION_TYPES if ultra_mode else AGGRESSIVE_RELATION_TYPES
    compact_relations = [
        relation
        for relation in relation_records
        if _relation_type(relation) in allowed_relation_types
        and (
            relation.get("source_id") in kept_ids
            or relation.get("target_resolved") in kept_ids
        )
    ]
    if ultra_mode:
        compact_index.extend(preserved_xsd_index)
        compact_details.extend(preserved_xsd_details)
        compact_relations.extend(preserved_xsd_relations)
    return compact_index, compact_details, compact_relations


def _compact_index_record(record: dict[str, Any], *, ultra_mode: bool, rich_java_mode: bool) -> dict[str, Any]:
    kind = record.get("kind")
    if kind == "java_type":
        compacted = dict(record)
        compacted.pop("roles", None)
        if rich_java_mode:
            compacted["imports"] = list(record.get("imports", [])[:10])
            compacted["fields"] = [_compact_field(field, ultra_mode=False) for field in list(record.get("fields", [])[:10])]
            compacted["methods"] = list(record.get("methods", [])[:10])
            compacted["constructors"] = list(record.get("constructors", [])[:4])
            compacted["used_types"] = list(record.get("used_types", [])[:12])
            compacted["called_methods"] = list(record.get("called_methods", [])[:6])
            compacted["summary"] = _truncate_string(record.get("summary"), 220)
            compacted["embedding_text"] = _truncate_string(record.get("embedding_text"), 320)
        else:
            compacted["imports"] = list(record.get("imports", [])[:(6 if ultra_mode else 12)])
            compacted["fields"] = [_compact_field(field, ultra_mode=ultra_mode) for field in list(record.get("fields", [])[:(6 if ultra_mode else 12)])]
            compacted["methods"] = list(record.get("methods", [])[:(6 if ultra_mode else 12)])
            compacted["constructors"] = list(record.get("constructors", [])[:(3 if ultra_mode else 6)])
            compacted["used_types"] = list(record.get("used_types", [])[:(8 if ultra_mode else 12)])
            compacted["called_methods"] = list(record.get("called_methods", [])[:(4 if ultra_mode else 8)])
            compacted["summary"] = _truncate_string(record.get("summary"), 160 if ultra_mode else 220)
            compacted["embedding_text"] = _truncate_string(record.get("embedding_text"), 220 if ultra_mode else 360)
        compacted["role_labels"] = list(record.get("role_labels", [])[:4])
        compacted["type_resolution_conflicts"] = list(record.get("type_resolution_conflicts", [])[:(2 if ultra_mode else 4)])
        return compacted
    if kind == "xml_tag_summary":
        compacted = dict(record)
        compacted["tags"] = [
            {
                "tag": item.get("tag"),
                "first_path": _truncate_string(item.get("first_path"), 100 if ultra_mode else 120),
                "attribute_names": list(item.get("attribute_names", [])[:(2 if ultra_mode else 4)]),
                "child_tags": list(item.get("child_tags", [])[:(2 if ultra_mode else 4)]),
            }
            for item in list(record.get("tags", [])[:(8 if ultra_mode else 12)])
        ]
        compacted["embedding_text"] = _truncate_string(record.get("embedding_text"), 180 if ultra_mode else 260)
        return compacted
    compacted = dict(record)
    if "embedding_text" in compacted:
        compacted["embedding_text"] = _truncate_string(compacted.get("embedding_text"), 220 if ultra_mode else 320)
    if "summary" in compacted:
        compacted["summary"] = _truncate_string(compacted.get("summary"), 160 if ultra_mode else 220)
    return compacted


def _keep_index_record(record: dict[str, Any], *, ultra_mode: bool, rich_java_mode: bool) -> bool:
    kind = record.get("kind")
    if kind not in AGGRESSIVE_INDEX_KINDS:
        return False
    if kind == "java_type":
        if rich_java_mode:
            return True
        role_labels = set(record.get("role_labels", []) or [])
        if role_labels.intersection({"entity", "repository", "controller", "service", "config", "client", "facade"}):
            return True
        name = str(record.get("name") or "")
        annotations = " ".join(record.get("annotations", []) or [])
        return (
            name.endswith(("Mapper", "Converter", "Facade", "Client", "Config"))
            or any(token in annotations for token in ("@Entity", "@Configuration", "@RestController", "@Controller", "@Service", "@Repository"))
        )
    if kind == "adoc_section":
        return float(record.get("importance_score") or 0.0) >= (3.2 if ultra_mode else 2.5)
    return True


def _compact_detail_record(record: dict[str, Any], *, ultra_mode: bool) -> dict[str, Any]:
    compacted: dict[str, Any] = {}
    for key, value in record.items():
        if key == "embedding_text":
            compacted[key] = _truncate_string(value, 180 if ultra_mode else 260)
            continue
        compacted[key] = _compact_value(value, depth=0, ultra_mode=ultra_mode)
    return compacted


def _compact_field(field: Any, *, ultra_mode: bool) -> Any:
    if not isinstance(field, dict):
        return field
    compacted = {
        "name": field.get("name"),
        "type": field.get("type"),
        "resolved_types": list(field.get("resolved_types", [])[:(3 if ultra_mode else 6)]),
        "annotations": list(field.get("annotations", [])[:(2 if ultra_mode else 4)]),
    }
    return {key: value for key, value in compacted.items() if value not in (None, [], {})}


def _compact_value(value: Any, *, depth: int, ultra_mode: bool) -> Any:
    if value is None:
        return value
    if isinstance(value, str):
        if ultra_mode:
            return _truncate_string(value, 160 if depth == 0 else 80)
        return _truncate_string(value, 240 if depth == 0 else 120)
    if isinstance(value, list):
        limit = (6 if depth == 0 else 4) if ultra_mode else (10 if depth == 0 else 6)
        return [_compact_value(item, depth=depth + 1, ultra_mode=ultra_mode) for item in value[:limit]]
    if isinstance(value, dict):
        compacted: dict[str, Any] = {}
        for index, (key, nested) in enumerate(value.items()):
            if index >= (8 if ultra_mode else 12):
                break
            compacted[key] = _compact_value(nested, depth=depth + 1, ultra_mode=ultra_mode)
        return compacted
    return value


def _truncate_string(value: Any, limit: int) -> Any:
    if not isinstance(value, str):
        return value
    return value[:limit]


def _relation_type(relation: dict[str, Any]) -> str:
    value = relation.get("relation") or relation.get("type")
    return str(value or "")


def _is_xsd_record(record: dict[str, Any]) -> bool:
    return str(record.get("kind") or "").startswith("xsd_")


def _is_xsd_relation(relation: dict[str, Any]) -> bool:
    if str(relation.get("file") or "").lower().endswith(".xsd"):
        return True
    source_kind = str(relation.get("source_kind") or "")
    target_resolved = str(relation.get("target_resolved") or "")
    return source_kind.startswith("xsd_") or target_resolved.startswith("xsd_")
