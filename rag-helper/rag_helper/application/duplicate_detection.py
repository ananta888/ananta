from __future__ import annotations

from itertools import combinations

from rag_helper.utils.ids import safe_id


def build_duplicate_report(index_records: list[dict], mode: str) -> tuple[dict | None, list[dict]]:
    if mode != "basic":
        return None, []

    groups: dict[str, list[dict]] = {}
    for record in index_records:
        signature = _duplicate_signature(record)
        if not signature:
            continue
        groups.setdefault(signature, []).append(record)

    duplicate_groups = [records for records in groups.values() if len(records) > 1]
    relations: list[dict] = []
    report_groups: list[dict] = []
    for records in duplicate_groups:
        record_refs = [{"id": record.get("id"), "file": record.get("file"), "kind": record.get("kind")} for record in records]
        report_groups.append({
            "signature": _duplicate_signature(records[0]),
            "record_count": len(records),
            "records": record_refs,
        })
        for left, right in combinations(records, 2):
            relations.append({
                "kind": "relation",
                "file": left.get("file"),
                "id": f"relation:{safe_id(left.get('id', ''), right.get('id', ''), 'duplicate_candidate')}",
                "source_id": left.get("id"),
                "source_kind": left.get("kind"),
                "source_name": left.get("file"),
                "relation": "duplicate_candidate",
                "target": right.get("file"),
                "target_resolved": right.get("id"),
                "weight": 1,
                "confidence": 0.75,
                "heuristic": "normalized_structure_signature",
                "duplicate_group_size": len(records),
                "duplicate_signature": _duplicate_signature(records[0]),
                "from": left.get("id"),
                "to": right.get("id"),
                "type": "duplicate_candidate",
            })

    return {
        "group_count": len(report_groups),
        "groups": report_groups,
    }, relations


def _duplicate_signature(record: dict) -> str | None:
    kind = record.get("kind")
    if kind == "java_type":
        fields = tuple(sorted(
            f"{field.get('name')}:{field.get('type')}"
            for field in record.get("fields", [])
            if field.get("name") and field.get("type")
        ))
        if len(fields) < 2:
            return None
        return f"java_type|{record.get('type_kind')}|{fields}"
    if kind == "xml_tag":
        attrs = tuple(sorted(record.get("attribute_names", [])))
        children = tuple(sorted(record.get("child_tags", [])))
        if not attrs and not children:
            return None
        return f"xml_tag|{record.get('tag')}|{attrs}|{children}"
    if kind in {"properties_file", "yaml_file"}:
        keys = tuple(sorted(record.get("keys", [])))
        if len(keys) < 2:
            return None
        return f"{kind}|{keys}"
    return None
