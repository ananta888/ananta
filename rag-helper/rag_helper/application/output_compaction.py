from __future__ import annotations

from typing import Any


AGGRESSIVE_INDEX_KINDS = {
    "java_type",
    "java_package_summary",
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


def compact_output_records(
    index_records: list[dict],
    detail_records: list[dict],
    relation_records: list[dict],
    mode: str,
) -> tuple[list[dict], list[dict], list[dict]]:
    if mode != "aggressive":
        return index_records, detail_records, relation_records

    compact_index = [
        _compact_index_record(record)
        for record in index_records
        if record.get("kind") in AGGRESSIVE_INDEX_KINDS
    ]
    compact_details = [
        _compact_detail_record(record)
        for record in detail_records
        if record.get("kind") in AGGRESSIVE_DETAIL_KINDS
    ]
    kept_ids = {record.get("id") for record in compact_index}
    kept_ids.update(record.get("id") for record in compact_details)
    compact_relations = [
        relation
        for relation in relation_records
        if _relation_type(relation) in AGGRESSIVE_RELATION_TYPES
        and (
            relation.get("source_id") in kept_ids
            or relation.get("target_resolved") in kept_ids
        )
    ]
    return compact_index, compact_details, compact_relations


def _compact_index_record(record: dict[str, Any]) -> dict[str, Any]:
    kind = record.get("kind")
    if kind == "java_type":
        compacted = dict(record)
        compacted.pop("roles", None)
        compacted["imports"] = list(record.get("imports", [])[:12])
        compacted["fields"] = [_compact_field(field) for field in list(record.get("fields", [])[:12])]
        compacted["methods"] = list(record.get("methods", [])[:12])
        compacted["constructors"] = list(record.get("constructors", [])[:6])
        compacted["used_types"] = list(record.get("used_types", [])[:12])
        compacted["called_methods"] = list(record.get("called_methods", [])[:8])
        compacted["role_labels"] = list(record.get("role_labels", [])[:6])
        compacted["type_resolution_conflicts"] = list(record.get("type_resolution_conflicts", [])[:4])
        compacted["summary"] = _truncate_string(record.get("summary"), 220)
        compacted["embedding_text"] = _truncate_string(record.get("embedding_text"), 360)
        return compacted
    if kind == "xml_tag_summary":
        compacted = dict(record)
        compacted["tags"] = [
            {
                "tag": item.get("tag"),
                "first_path": _truncate_string(item.get("first_path"), 120),
                "attribute_names": list(item.get("attribute_names", [])[:4]),
                "child_tags": list(item.get("child_tags", [])[:4]),
            }
            for item in list(record.get("tags", [])[:12])
        ]
        compacted["embedding_text"] = _truncate_string(record.get("embedding_text"), 260)
        return compacted
    compacted = dict(record)
    if "embedding_text" in compacted:
        compacted["embedding_text"] = _truncate_string(compacted.get("embedding_text"), 320)
    if "summary" in compacted:
        compacted["summary"] = _truncate_string(compacted.get("summary"), 220)
    return compacted


def _compact_detail_record(record: dict[str, Any]) -> dict[str, Any]:
    compacted: dict[str, Any] = {}
    for key, value in record.items():
        if key == "embedding_text":
            compacted[key] = _truncate_string(value, 260)
            continue
        compacted[key] = _compact_value(value, depth=0)
    return compacted


def _compact_field(field: Any) -> Any:
    if not isinstance(field, dict):
        return field
    compacted = {
        "name": field.get("name"),
        "type": field.get("type"),
        "resolved_types": list(field.get("resolved_types", [])[:6]),
        "annotations": list(field.get("annotations", [])[:4]),
    }
    return {key: value for key, value in compacted.items() if value not in (None, [], {})}


def _compact_value(value: Any, *, depth: int) -> Any:
    if value is None:
        return value
    if isinstance(value, str):
        return _truncate_string(value, 240 if depth == 0 else 120)
    if isinstance(value, list):
        limit = 10 if depth == 0 else 6
        return [_compact_value(item, depth=depth + 1) for item in value[:limit]]
    if isinstance(value, dict):
        compacted: dict[str, Any] = {}
        for index, (key, nested) in enumerate(value.items()):
            if index >= 12:
                break
            compacted[key] = _compact_value(nested, depth=depth + 1)
        return compacted
    return value


def _truncate_string(value: Any, limit: int) -> Any:
    if not isinstance(value, str):
        return value
    return value[:limit]


def _relation_type(relation: dict[str, Any]) -> str:
    value = relation.get("relation") or relation.get("type")
    return str(value or "")
