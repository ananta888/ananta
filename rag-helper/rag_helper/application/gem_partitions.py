from __future__ import annotations

from typing import Any


def build_gem_partition_records(
    index_records: list[dict],
    detail_records: list[dict],
    relation_records: list[dict],
    mode: str,
) -> list[dict]:
    if mode != "domain":
        return []

    records: list[dict] = []
    for record in index_records:
        domain = _classify_domain(record)
        if domain is None:
            continue
        records.append({
            "domain": domain,
            "source_kind": record.get("kind"),
            "file": record.get("file"),
            "id": record.get("id"),
            "title": _record_title(record),
            "summary": _record_summary(record),
            "embedding_text": _truncate(record.get("embedding_text"), 260),
        })

    kept_ids = {record.get("id") for record in records}
    for record in detail_records:
        domain = _classify_domain(record)
        if domain is None:
            continue
        parent_id = record.get("parent_id")
        if parent_id and parent_id not in kept_ids:
            continue
        records.append({
            "domain": domain,
            "source_kind": record.get("kind"),
            "file": record.get("file"),
            "id": record.get("id"),
            "title": _record_title(record),
            "summary": _record_summary(record),
            "embedding_text": _truncate(record.get("embedding_text"), 220),
        })

    for record in relation_records:
        domain = _classify_relation_domain(record)
        if domain is None:
            continue
        records.append({
            "domain": domain,
            "source_kind": "relation",
            "file": record.get("file"),
            "id": record.get("id"),
            "title": str(record.get("relation") or record.get("type") or "relation"),
            "summary": _relation_summary(record),
        })
    return records


def _classify_domain(record: dict[str, Any]) -> str | None:
    kind = str(record.get("kind") or "")
    role_labels = set(record.get("role_labels", []) or [])
    if kind in {"xml_tag_summary", "xml_file"}:
        return "configuration"
    if kind in {"jpa_entity_chunk", "xsd_file", "xsd_complex_type", "xsd_complex_type_detail", "xsd_simple_type", "xsd_root_element", "xsd_schema_chunk"}:
        return "data-model"
    if kind in {"adoc_section", "adoc_architecture_chunk", "adoc_section_detail", "md_section"}:
        return "docs"
    if "controller" in role_labels:
        return "api"
    if "service" in role_labels:
        return "service"
    if "repository" in role_labels or "entity" in role_labels:
        return "data-model"
    if "config" in role_labels:
        return "configuration"
    if "client" in role_labels or "facade" in role_labels:
        return "integration"
    if kind in {"properties_entry", "yaml_entry"}:
        return "configuration"
    if kind == "sql_statement":
        return "data-model"
    return None


def _classify_relation_domain(record: dict[str, Any]) -> str | None:
    relation = str(record.get("relation") or record.get("type") or "")
    if relation in {"spring_configuration", "declares_bean", "bean_factory_method"}:
        return "configuration"
    if relation in {"injects_dependency"}:
        return "service"
    if relation.startswith("jpa_") or relation.startswith("contains_complex_type") or relation.startswith("contains_simple_type") or relation.startswith("contains_root_element") or relation.startswith("contains_element_") or relation.startswith("has_attribute_type") or relation == "restricted_by":
        return "data-model"
    if relation in {"extends", "implements", "field_type_uses"}:
        return "architecture"
    return None


def _record_title(record: dict[str, Any]) -> str:
    return str(
        record.get("name")
        or record.get("title")
        or record.get("tag")
        or record.get("file")
        or record.get("id")
        or "record"
    )


def _record_summary(record: dict[str, Any]) -> str:
    if record.get("summary"):
        return _truncate(record.get("summary"), 220)
    if record.get("kind") == "xml_tag_summary":
        tags = [item.get("tag") for item in list(record.get("tags", [])[:8]) if item.get("tag")]
        return f"XML summary with {record.get('tag_count', 0)} tags. Top tags: {', '.join(tags) or 'none'}."
    if record.get("kind") == "java_type":
        return (
            f"{record.get('type_kind', 'type')} {record.get('name')} "
            f"roles={','.join(record.get('role_labels', []) or []) or 'none'} "
            f"methods={len(record.get('methods', []) or [])} "
            f"fields={len(record.get('fields', []) or [])}"
        )
    return _truncate(str(record.get("file") or record.get("id") or "record"), 220)


def _relation_summary(record: dict[str, Any]) -> str:
    return _truncate(
        f"{record.get('source_name') or record.get('source_id')} "
        f"{record.get('relation') or record.get('type')} "
        f"{record.get('target') or record.get('target_resolved')}",
        220,
    )


def _truncate(value: Any, limit: int) -> str:
    return str(value or "")[:limit]
